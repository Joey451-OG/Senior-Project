import psutil
import apscheduler

from PROJECTTypes import TemperatureReading, UsersSession

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")


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


def getUsers() -> dict:
    users = psutil.users()
    packet = {"name": "Users Websocket", "data": None}
    ret_list = []

    for u in users:
        ret_list.append(UsersSession(
            u.name, 
            u.terminal, 
            u.host, 
            u.started, 
            int(u.pid)
        ))

    packet["data"] = ret_list
    return packet

