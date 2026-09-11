import asyncio
import websockets
import sys

async def listen(socket: str, domain: str):
    async with websockets.connect(f"ws://{domain}:8000{socket_path}") as ws:
        while True:
            message = await ws.recv()
            print(message)


if __name__ == "__main__":
    socket_path = sys.argv[1]
    domain = sys.argv[2] if len(sys.argv) == 3 else "localhost"
    asyncio.run(listen(socket_path, domain))
