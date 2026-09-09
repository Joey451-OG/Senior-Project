import psutil
import apscheduler
import backend.PROJECTTypes as PTypes

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")


def get_cpu_utilization() -> float:
    return psutil.cpu_percent()

def get_cpu_temperatures() -> dict[str, list[PTypes.TemperatureReading]] | None:
    temps = psutil.sensors_temperatures()

    if temps:
        for name, entries in temps.items():
            if name not in CPU_SENSOR_NAME:
                continue

            ret_dictionary = {}

            for entry in entries:

                ret_dictionary[entry.label] = PTypes.TemperatureReading(entry.current, entry.high, entry.critical)

            return ret_dictionary


def get_users() -> list[str | float | None]:
    users = psutil.users()
    ret_list = []

    for u in users:
        app = []

        app.append(u.name)
        app.append(u.terminal)
        app.append(u.host)
        app.append(u.started)
        app.append(u.pid)

        ret_list.append(app)

    return ret_list

