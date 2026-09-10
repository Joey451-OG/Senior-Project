import asyncio
import websockets
import sys

async def listen(socket: str):
    async with websockets.connect(f"ws://localhost:8000{socket_path}") as ws:
        while True:
            message = await ws.recv()
            print(message)


if __name__ == "__main__":
    socket_path = sys.argv[1]
    asyncio.run(listen(socket_path))
