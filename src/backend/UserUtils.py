import psutil
import getpass
import apscheduler

from PROJECTTypes import TemperatureReading, UsersSession, UserProcesses

# Setup
psutil.cpu_percent()
CPU_SENSOR_NAME = ("coretemp", "k10temp", "cpu_thermal")

# Globals
logged_in_users = []

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


def getUserProcs() -> dict:
    packet = {"name": "Users Websocket", "data": None}
    ret_list = []

    user = getpass.getuser()
    
    for proc in psutil.process_iter():
        if proc.username() != user:
            continue

        info = proc.as_dict(attrs=[
            'pid',
            'name',
            'username',
            'exe',
            'cpu_percent',
            'memory_percent'
        ])

        process = UserProcesses(
            int(info['pid']),
            info["name"],
            info["username"],
            info["exe"],
            info["cpu_percent"],
            info["memory_percent"]
        )

        ret_list.append(process)

    packet["data"] = ret_list
    return packet

