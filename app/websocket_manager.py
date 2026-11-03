import asyncio
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.active_connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            connections = list(self.active_connections)

        async def send_one(websocket: WebSocket) -> None:
            try:
                await asyncio.wait_for(
                    websocket.send_json(message),
                    timeout=5.0,
                )
            except Exception:
                await self.disconnect(websocket)

        await asyncio.gather(
            *(send_one(ws) for ws in connections),
            return_exceptions=True,
        )


manager = ConnectionManager()