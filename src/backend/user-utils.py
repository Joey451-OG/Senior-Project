import psutil
import apscheduler

from backend.PROJECTTypes import TemperatureReading, UsersSession

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")


def getCpuUtilization() -> float:
    return psutil.cpu_percent()

def getCpuTemperatures() -> dict[str, list[TemperatureReading]] | None:
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


def getUsers() -> list[str | float | None]:
    users = psutil.users()
    ret_list = []

    for u in users:
        ret_list.append(UsersSession(
            u.name, 
            u.terminal, 
            u.host, 
            u.started, 
            u.pid
        ))

    return ret_list

