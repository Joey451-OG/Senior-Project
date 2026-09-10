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
