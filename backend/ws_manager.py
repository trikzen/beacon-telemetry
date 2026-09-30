from fastapi import WebSocket


class ConnectionManager:
    """Keeps track of connected browsers and pushes messages to all of them."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    @property
    def count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to every connected client.

        A client that has gone away is dropped quietly, so one dead browser
        tab can never make an incoming event fail.
        """
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(websocket)
