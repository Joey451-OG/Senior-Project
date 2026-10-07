from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)

        for dead_connection in dead_connections:
            self.disconnect(dead_connection)


websocket_router = APIRouter(prefix="/ws", tags=["websockets"])

cpu_load_connection_manager = WSConnectionManager()
cpu_temperature_connection_manager = WSConnectionManager()
user_procs_connection_manager = WSConnectionManager()

# How long we wait for any client message before treating the socket as idle
# and probing it with a ping. This is also effectively how quickly a
# half-open connection (e.g. client killed with ^C) gets detected.
heartbeat_interval_seconds = 10

# How long we wait for a pong (or any client message) after sending a ping
# before we give up on the connection and close it server-side.
heartbeat_timeout_seconds = 5


# --- Broadcast jobs (scheduled by main.py's lifespan) ---

async def broadcastCpuLoad():
    await cpu_load_connection_manager.broadcast(UserUtils.getCpuUtilization())

async def broadcastCpuTemperatures():
    await cpu_temperature_connection_manager.broadcast(UserUtils.getCpuTemperatures())

async def broadcastUserProcs():
    await user_procs_connection_manager.broadcast(UserUtils.getUserProcs())


def registerBroadcastJobs(scheduler: AsyncIOScheduler, broadcast_interval_seconds: int = 1):
    """
    Adds this router's periodic broadcast jobs to the app-owned scheduler.
    The router doesn't own the scheduler so that future routers can share
    it and main.py stays the single place that starts and stops it.
    """
    scheduler.add_job(broadcastCpuLoad, "interval", seconds=broadcast_interval_seconds,
                      id="cpu_load_broadcast", replace_existing=True)
    scheduler.add_job(broadcastCpuTemperatures, "interval", seconds=broadcast_interval_seconds,
                      id="cpu_temperature_broadcast", replace_existing=True)
    scheduler.add_job(broadcastUserProcs, "interval", seconds=broadcast_interval_seconds,
                      id="user_procs_broadcast", replace_existing=True)


# --- Heartbeat ---

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
    messages, but times out periodically to ping the client and close+reap
    the connection if it turns out to be half-open (dead peer, no proper
    close frame -- e.g. a tester script killed with ^C).
    """
    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=heartbeat_interval_seconds)
            except asyncio.TimeoutError:
                is_client_alive = await pingClientAndAwaitReply(ws)
                if not is_client_alive:
                    await ws.close()
                    break
    except WebSocketDisconnect:
        print("[websocket_router.py]: WebSocketDisconnect thrown, disconnecting")
    finally:
        connection_manager.disconnect(ws)


# --- Routes (final paths: /ws/cpu-load, /ws/cpu-temp, /ws/user-procs) ---

@websocket_router.websocket("/cpu-load")
async def cpuLoadSocket(ws: WebSocket):
    await cpu_load_connection_manager.connect(ws)
    await runWebsocketHeartbeatLoop(ws, cpu_load_connection_manager)

@websocket_router.websocket("/cpu-temp")
async def cpuTemperatureSocket(ws: WebSocket):
    await cpu_temperature_connection_manager.connect(ws)
    await runWebsocketHeartbeatLoop(ws, cpu_temperature_connection_manager)

@websocket_router.websocket("/user-procs")
async def userProcsSocket(ws: WebSocket):
    await user_procs_connection_manager.connect(ws)
    await runWebsocketHeartbeatLoop(ws, user_procs_connection_manager)