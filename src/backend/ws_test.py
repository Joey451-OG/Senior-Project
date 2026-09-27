import asyncio
import json
import sys
import websockets


async def listen(socket_path: str, domain: str, port: int):
    async with websockets.connect(f"ws://{domain}:{port}{socket_path}") as ws:
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
   # socket_path = sys.argv[1]
   # domain = sys.argv[2] if len(sys.argv) == 3 else "localhost"
   # asyncio.run(listen(socket_path, domain))

    possible_flags = ["-d", "-p"]
    path = sys.argv[1]
    domain = "localhost"
    port = 3002

    for flag in possible_flags:
        for i in range(len(sys.argv)):
            if sys.argv[i].lower() == flag:
                error_word = ""
                try:
                    match flag:
                        case "-d":
                            error_word = "domain"
                            domain = sys.argv[i + 1] if type(sys.argv[i + 1]) == str else raise ValueError
                        case "-p":
                            error_word = "port"
                            port = sys.argv[i + 1] if type(sys.argv[i + 1]) == int else raise ValueError
                except IndexError or ValueError:
                    print(f"ERROR: Please provide a/an {error_word} with {flag}")
                    return 2

   asyncio.run(listen(path, domain, port)) 




