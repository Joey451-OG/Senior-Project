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

class UserProcesses(NamedTuple):
    pid: int | None
    name: str | None
    username: str | None
    exe: str | None
    cpu_percent: str | None
    memory_percent: str | None


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
