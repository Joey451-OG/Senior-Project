from typing import NamedTuple

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
