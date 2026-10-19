import psutil
import getpass
import apscheduler
import crontab

from PROJECTTypes import TemperatureReading, UsersSession, UserProcesses

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")

CPU_COUNT = psutil.cpu_count() or 1

# Globals
logged_in_users = []

# io counters from the previous getUserProcs() call, used to compute per-interval rates
previous_proc_io: dict[tuple[int, float], tuple[int, int]] = {}
previous_system_disk_bytes: int | None = None

# Utility Function(s)
# TODO: Wite a function to get all logged in users. This will run every minute.

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

