import json
import os
import psutil
import subprocess
import getpass
import pwd
import time
import apscheduler
import crontab

from PROJECTTypes import TemperatureReading, UsersSession, UserProcesses, LoggedInUser

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")

CPU_COUNT = psutil.cpu_count() or 1

FALLBACK_SHELL_NAMES = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh"}

# logind session classes that are real logins ("manager" is the user's systemd instance, not a login)
LOGIN_SESSION_CLASSES = {"user", "user-early"}
# "closing" sessions were logged out of but still have leftover processes (e.g. a detached tmux)
LOGGED_IN_SESSION_STATES = {"active", "online"}
LOGINCTL_TIMEOUT_SECONDS = 5

# Globals
logged_in_users = []

# io counters from the previous getUserProcs() call, used to compute per-interval rates
previous_proc_io: dict[tuple[int, float], tuple[int, int]] = {}
previous_system_disk_bytes: int | None = None

# Utility Function(s)
def readShellNames() -> set[str]:
    """Basenames of the shells listed in /etc/shells, falling back to the common ones."""
    try:
        with open("/etc/shells", encoding="utf-8") as shells_file:
            shell_names = {os.path.basename(line.strip()) for line in shells_file
                           if line.strip() and not line.startswith("#")}
    except OSError:
        return FALLBACK_SHELL_NAMES
    return shell_names or FALLBACK_SHELL_NAMES


def formatDuration(total_seconds: int) -> str:
    """e.g. 2d 3h 14m; anything under a minute is shown as 0m."""
    days, remainder = divmod(max(total_seconds, 0), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def lookupLoginShell(username: str) -> str | None:
    try:
        return os.path.basename(pwd.getpwnam(username).pw_shell) or None
    except KeyError:
        return None

# Data Collection
def getCpuUtilization() -> dict:
    payload = {"type": "cpu", "value": psutil.cpu_percent()}
    return payload

def getCpuTemperatures() -> dict:
    temps = psutil.sensors_temperatures()

    if temps:
        for name, entries in temps.items():
            if name not in CPU_SENSOR_NAME:
                continue

            ret_dictionary = {}

            for entry in entries:

                ret_dictionary[entry.label] = TemperatureReading(
                                                entry.current,
                                                entry.high,
                                                entry.critical
                                            )

            return ret_dictionary

    return {}


def runLoginctl(*arguments: str) -> str:
    return subprocess.run(["loginctl", "--no-pager", *arguments], capture_output=True, text=True,
                          check=True, timeout=LOGINCTL_TIMEOUT_SECONDS).stdout


def readLogindSessions() -> list[dict]:
    """
    Current login sessions from systemd-logind. Used instead of psutil.users(),
    which only reads utmp, and systems without utmp (e.g. systemd 259 Ubuntu) have none.
    Each dict has: id, username, scope (cgroup unit name) and started (epoch seconds).
    """
    listed_sessions = json.loads(runLoginctl("list-sessions", "--json=short"))
    session_ids = [session["session"] for session in listed_sessions
                   if session["class"] in LOGIN_SESSION_CLASSES]
    if not session_ids:
        return []

    # One call for every session; the output is one KEY=value block per session, separated by blank lines.
    show_output = runLoginctl("show-session", *session_ids, "-p", "Id", "-p", "Name", "-p", "State",
                              "-p", "Scope", "-p", "TimestampMonotonic")

    # logind only exposes the login time formatted for humans or as CLOCK_MONOTONIC microseconds;
    # convert the latter. This can drift if the machine has been suspended since the login.
    monotonic_to_epoch_offset = time.time() - time.clock_gettime(time.CLOCK_MONOTONIC)

    sessions = []
    for block in show_output.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if properties.get("State") not in LOGGED_IN_SESSION_STATES:
            continue
        sessions.append({
            "id": properties["Id"],
            "username": properties["Name"],
            "scope": properties["Scope"],
            "started": monotonic_to_epoch_offset + int(properties["TimestampMonotonic"]) / 1_000_000,
        })
    return sessions


def readCgroupUnit(pid: int) -> str | None:
    """The last component of a process's cgroup v2 path, e.g. session-12.scope."""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as cgroup_file:
            for line in cgroup_file:
                if line.startswith("0::"):
                    return os.path.basename(line.strip())
    except OSError:
        return None
    return None


def getLoggedInUsers() -> dict:
    """
    One entry per logged-in user (a user with several sessions is listed once).
    Sessions come from systemd-logind; a session's shell is the newest shell that has
    a terminal inside the session's cgroup. tmux panes are not counted, because tmux
    runs them in their own scopes outside the login session.
    """
    packet = {"name": "Logged In Users", "data": None}
    now = time.time()
    shell_names = readShellNames()

    try:
        sessions = readLogindSessions()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        print(f"[UserUtils.py]: could not read logind sessions: {error}")
        sessions = []

    sessions_by_user: dict[str, list[dict]] = {}
    for session in sessions:
        sessions_by_user.setdefault(session["username"], []).append(session)

    # One pass over all processes: count per user, and remember the newest shell per cgroup scope.
    process_counts: dict[str, int] = {}
    newest_shell_by_scope: dict[str, tuple[float, str]] = {}
    for proc in psutil.process_iter(['pid', 'username', 'name', 'terminal', 'create_time']):
        info = proc.info
        username = info['username']
        if username not in sessions_by_user:
            continue
        process_counts[username] = process_counts.get(username, 0) + 1

        if info['terminal'] and info['name'] in shell_names:
            scope = readCgroupUnit(info['pid'])
            current = newest_shell_by_scope.get(scope)
            if scope and (current is None or info['create_time'] > current[0]):
                newest_shell_by_scope[scope] = (info['create_time'], info['name'])

    ret_list = []
    for username, user_sessions in sessions_by_user.items():
        login_time = min(session["started"] for session in user_sessions)
        logged_in_seconds = int(now - login_time)

        # Check sessions newest first; sessions without a terminal shell fall through to the login shell.
        shell = None
        for session in sorted(user_sessions, key=lambda session: session["started"], reverse=True):
            newest_shell = newest_shell_by_scope.get(session["scope"])
            if newest_shell is not None:
                shell = newest_shell[1]
                break
        if shell is None:
            shell = lookupLoginShell(username)

        ret_list.append(LoggedInUser(
            username,
            login_time,
            formatDuration(logged_in_seconds),
            logged_in_seconds,
            len(user_sessions),
            shell,
            process_counts.get(username, 0)
        ))

    ret_list.sort(key=lambda user: user.login_time)
    packet["data"] = ret_list
    return packet


def readIoCounters(proc: psutil.Process) -> tuple[int, int] | None:
    """
    Returns (disk bytes, total I/O chars) for a process, or None when they can't be read.
    Without root this only works for processes owned by the backend's own user.
    read/write_bytes is what actually hit the block device; read/write_chars also
    counts pipes, sockets and page-cache hits.
    """
    try:
        counters = proc.io_counters()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None
    return (counters.read_bytes + counters.write_bytes,
            counters.read_chars + counters.write_chars)


def getUserProcs() -> dict:
    global previous_proc_io, previous_system_disk_bytes
    packet = {"name": "Users Websocket", "data": None}
    ret_list = []

    system_disk = psutil.disk_io_counters()
    system_disk_bytes = system_disk.read_bytes + system_disk.write_bytes if system_disk else None
    system_disk_delta = None
    if system_disk_bytes is not None and previous_system_disk_bytes is not None:
        system_disk_delta = system_disk_bytes - previous_system_disk_bytes

    # First pass: collect info and per-process io deltas since the last call.
    # Keyed on (pid, create_time) so a reused pid doesn't inherit old counters.
    samples = []
    current_proc_io = {}
    total_io_delta = 0
    for proc in psutil.process_iter(['pid', 'name', 'username', 'exe', 'cpu_percent',
                                     'memory_percent', 'create_time']):
        info = proc.info
        disk_delta = io_delta = None

        counters = readIoCounters(proc)
        if counters is not None:
            key = (info['pid'], info['create_time'])
            current_proc_io[key] = counters
            previous = previous_proc_io.get(key)
            if previous is not None:
                disk_delta = counters[0] - previous[0]
                io_delta = counters[1] - previous[1]
                total_io_delta += io_delta

        samples.append((info, disk_delta, io_delta))

    previous_proc_io = current_proc_io
    previous_system_disk_bytes = system_disk_bytes

    # Second pass: turn deltas into shares of the machine-wide totals
    for info, disk_delta, io_delta in samples:
        disk_percent = None
        if disk_delta is not None and system_disk_delta is not None:
            # Process write_bytes is counted when dirtied, the device's when flushed, so clamp
            disk_percent = min(100.0, 100 * disk_delta / system_disk_delta) if system_disk_delta > 0 else 0.0

        io_percent = None
        if io_delta is not None:
            io_percent = 100 * io_delta / total_io_delta if total_io_delta > 0 else 0.0

        # psutil reports per-process CPU relative to one core (up to 100 * cores);
        # divide so it's comparable with the overall CPU load gauge.
        cpu_percent = info['cpu_percent'] / CPU_COUNT if info['cpu_percent'] is not None else None

        process = UserProcesses(
            int(info['pid']),
            info["name"],
            info["username"],
            info["exe"],
            roundPercent(cpu_percent),
            roundPercent(info["memory_percent"]),
            roundPercent(disk_percent),
            roundPercent(io_percent)
        )

        ret_list.append(process)

    packet["data"] = ret_list
    return packet


def roundPercent(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None

def getCurrentCronJobs():

    ret_dict = {"name": "Cron Jobs Websocket", "data": None}

    # as of now, this only gets one user. This will need to be updated for all logged in users
    user = getpass.getuser()
    cron = crontab.CronTab(user=user)


    for job in cron:
        data = []
        data.append(user)
        data.append(job.render())

        ret_dict["data"].append(data)

    return ret_dict

