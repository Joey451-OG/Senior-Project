import asyncio
import json
import sys
import websockets


async def listen(socket_path: str, domain: str):
    async with websockets.connect(f"ws://{domain}:8000{socket_path}") as ws:
        while True:
            message = await ws.recv()
            print(message)

            is_heartbeat_ping = isHeartbeatPing(str(message))
            if is_heartbeat_ping:
                await ws.send("pong")


def isHeartbeatPing(message: str) -> bool:
    try:
        parsed_message = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return False

    return isinstance(parsed_message, dict) and parsed_message.get("type") == "ping"


if __name__ == "__main__":
    socket_path = sys.argv[1]
    domain = sys.argv[2] if len(sys.argv) == 3 else "localhost"
    asyncio.run(listen(socket_path, domain))
