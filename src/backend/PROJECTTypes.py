from typing import NamedTuple

class TemperatureReading(NamedTuple):
    current: float | None
    high: float | None
    critical: float | None

