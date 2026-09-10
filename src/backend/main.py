from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import psutil
import json
import asyncio
import UserUtils

class ConnectionManager:
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

manager = ConnectionManager()
scheduler = AsyncIOScheduler()

async def pollAndBroadcast():
    await manager.broadcast(UserUtils.getCpuUtilization())


@asynccontextmanager
async def lifespan(api: FastAPI):
    scheduler.add_job(pollAndBroadcast, "interval", seconds=1, id="cpu_broadcast")
    scheduler.start()

    yield

    scheduler.shutdown()

api = FastAPI(lifespan=lifespan)


@api.websocket("/ws/cpu-load")
async def cpuLoadSocket(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

