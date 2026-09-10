from fastapi.testclient import TestClient

from main import api

def test_WebSocket():

    with TestClient(api) as client:
        with client.websocket_connect("/ws/cpu-load") as websocket:
            data = websocket.receive_json()
            assert data == {"type": "cpu"}
