from typing import NamedTuple
from pydantic import BaseModel, Field

class TemperatureReading(NamedTuple):
    current: float | None
    high: float | None
    critical: float | None

class UsersSession(NamedTuple):
    name: str | None
    terminal: str | None
    host: str | None
    started: float | None
    pid: int | None

class LoggedInUser(NamedTuple):
    username: str
    login_time: float             # epoch seconds of the user's earliest current session
    logged_in_for: str            # e.g. "2d 3h 14m", measured from login_time
    logged_in_seconds: int
    session_count: int
    shell: str | None             # shell running in the newest session, else the login shell from passwd
    process_count: int            # every process the user owns, not just ones in their sessions

class UserProcesses(NamedTuple):
    pid: int | None
    name: str | None
    username: str | None
    exe: str | None
    # All percentages are a share of the whole machine over the last sample interval
    cpu_percent: float | None
    memory_percent: float | None
    disk_percent: float | None   # None when the process's io counters aren't readable
    io_percent: float | None


# --- Cron (shapes returned by the privileged cron helper, re-exposed by /papi) ---

class CronJobEntry(BaseModel):
    # The helper sends "isEnabled"; the API exposes it as is_enabled.
    schedule: str
    command: str
    comment: str
    is_enabled: bool = Field(validation_alias="isEnabled")

class CrontabSnapshot(BaseModel):
    username: str
    content_hash: str
    enabled_job_count: int

class UserCrontab(CrontabSnapshot):
    jobs: list[CronJobEntry]

class CrontabListing(BaseModel):
    version: int
    crontabs: list[UserCrontab]
    failed_usernames: list[str]

class CronChangeEvent(BaseModel):
    version: int
    changed_usernames: list[str]

class CrontabContent(BaseModel):
    username: str
    content: str
    content_hash: str

class CrontabWriteRequest(BaseModel):
    content: str
    # content_hash of the version the editor started from; the write is refused if it no longer matches.
    expected_hash: str

class KilledProcess(BaseModel):
    pid: int
    username: str
    signal: str


# --- Admin commands (from /etc/waru/sudo_commands, via the privileged run-commands helper) ---

class AdminCommand(BaseModel):
    command_id: str
    command: str

class AdminCommandListing(BaseModel):
    version: int
    commands: list[AdminCommand]
    # Set when the helper refused the commands file (missing, unsafe permissions, ...); commands is then empty.
    error: str | None

class AdminCommandsChangeEvent(BaseModel):
    version: int
