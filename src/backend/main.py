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
        # Guard against double-removal: broadcast()'s own dead-connection
        # reaping and the heartbeat loop can both try to disconnect the
        # same socket in a race.
        if websocket in self.active_connections:
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
ws_users = WSConnectionManager()
scheduler = AsyncIOScheduler()

# How long we wait for any client message before treating the socket as idle
# and probing it with a ping. This is also effectively how quickly a
# half-open connection (e.g. client killed with ^C) gets detected.
heartbeat_interval_seconds = 10

# How long we wait for a pong (or any client message) after sending a ping
# before we give up on the connection and close it server-side.
heartbeat_timeout_seconds = 5


# What I do for the sake of containerization
async def getCpuLoad():
    await ws_cpu_load_man.broadcast(UserUtils.getCpuUtilization())

async def getCpuTemps():
    await ws_cpu_temp_man.broadcast(UserUtils.getCpuTemperatures())

async def getUsers():
    await ws_users.broadcast(UserUtils.getUsers())


async def pingClientAndAwaitReply(ws: WebSocket) -> bool:
    """
    Sends an application-level ping and waits briefly for any reply from
    the client. A half-open TCP connection usually won't fail on send() --
    the OS will happily buffer into a dead pipe for a while -- so the
    absence of a reply within heartbeat_timeout_seconds, not an exception,
    is what actually signals a dead client.
    """
    try:
        await ws.send_json({"type": "ping"})
        await asyncio.wait_for(ws.receive_text(), timeout=heartbeat_timeout_seconds)
        return True
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, ConnectionError):
        return False


async def runWebsocketHeartbeatLoop(ws: WebSocket, connection_manager: WSConnectionManager):
    """
    Shared receive loop for a single websocket connection. Waits for client
    messages as before, but times out periodically to ping the client and
    close+reap the connection if it turns out to be half-open (dead peer,
    no proper close frame -- e.g. a tester script killed with ^C).
    """
    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=heartbeat_interval_seconds)
            except asyncio.TimeoutError:
                print(f"[main.py]: sent ping")
                is_client_alive = await pingClientAndAwaitReply(ws)
                if not is_client_alive:
                    await ws.close()
                    break
    except WebSocketDisconnect:
        print(f"[main.py]: WebSocketDisconnect thrown")
        print(f"[main.py]: disconnecting")
        connection_manager.disconnect(ws)
        pass
    finally:
        connection_manager.disconnect(ws)


@asynccontextmanager
async def lifespan(api: FastAPI):
    scheduler.add_job(getCpuLoad, "interval", seconds=1, id="cpu_broadcast")
    scheduler.add_job(getCpuTemps, "interval", seconds=1, id="cpu_temp")
    scheduler.add_job(getUsers, "interval", seconds=1, id="users")

    scheduler.start()

    yield

    scheduler.shutdown()

api = FastAPI(lifespan=lifespan)



@api.websocket("/ws/cpu-load")
async def cpuLoadSocket(ws: WebSocket):
    await ws_cpu_load_man.connect(ws)
    await runWebsocketHeartbeatLoop(ws, ws_cpu_load_man)

@api.websocket("/ws/cpu-temp")
async def cpuTemperatureSocket(ws: WebSocket):
    await ws_cpu_temp_man.connect(ws)
    await runWebsocketHeartbeatLoop(ws, ws_cpu_temp_man)


@api.websocket("/ws/users")
async def usersSocket(ws: WebSocket):
    await ws_users.connect(ws)
    await runWebsocketHeartbeatLoop(ws, ws_users)