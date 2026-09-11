from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import psutil
import json
import asyncio
import UserUtils

class WSConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)

        for d in dead:
            self.disconnect(d)

ws_cpu_load_man = WSConnectionManager()
ws_cpu_temp_man = WSConnectionManager()
scheduler = AsyncIOScheduler()


# What I do for the sake of containerization
async def getCpuLoad():
    await ws_cpu_load_man.broadcast(UserUtils.getCpuUtilization())

async def getCpuTemps():
    await ws_cpu_temp_man.broadcast(UserUtils.getCpuTemperatures()),
    
@asynccontextmanager
async def lifespan(api: FastAPI):
    scheduler.add_job(getCpuLoad, "interval", seconds=1, id="cpu_broadcast")
    scheduler.add_job(getCpuTemps, "interval", seconds=1, id="cpu_temp")


    scheduler.start()

    yield

    scheduler.shutdown()

api = FastAPI(lifespan=lifespan)



# NOTE: It may be worth combining these two
@api.websocket("/ws/cpu-load")
async def cpuLoadSocket(ws: WebSocket):
    await ws_cpu_load_man.connect(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_cpu_load_man.disconnect(ws)

@api.websocket("/ws/cpu-temp")
async def cpuTemperatureSocket(ws: WebSocket):
    await ws_cpu_temp_man.connect(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_cpu_load_man.disconnect(ws)


    

