import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db import get_event, get_recent_events, init_db, save_event
from models import Event
from ws_manager import ConnectionManager

BASE_DIR = Path(__file__).resolve().parent
# The dashboard sits next to this folder. Set BEACON_DASHBOARD_DIR to use another location.
DASHBOARD_DIR = Path(os.environ.get("BEACON_DASHBOARD_DIR", BASE_DIR.parent / "dashboard"))
manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create the table on startup if it's missing
    yield


app = FastAPI(title="beacon-telemetry", lifespan=lifespan)


def _store_event(event: Event) -> dict:
    """Blocking database work: save the event and read it back."""
    return get_event(save_event(event))


async def handle_event(event: Event) -> dict:
    """The single place where events get processed: save, then broadcast.

    HTTP calls it now; MQTT can call it later.
    """
    # The database call is blocking, so run it in a worker thread
    # to keep the server responsive to other devices and browsers.
    stored = await run_in_threadpool(_store_event, event)
    await manager.broadcast({"type": "event", "event": stored})
    return stored


@app.get("/", include_in_schema=False)
def dashboard():
    """The dashboard page: http://localhost:8000"""
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "message": "telemetry server running"}


@app.post("/events")
async def receive_event(event: Event):
    stored = await handle_event(event)
    return {"status": "accepted", "id": stored["id"], "event": stored}


@app.get("/events")
def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    device_id: str | None = None,
):
    return get_recent_events(limit=limit, device_id=device_id)


@app.websocket("/ws")
async def live_feed(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "hello", "message": "connected"})
        while True:
            await websocket.receive_text()  # keeps the connection open
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# The dashboard's CSS and JavaScript, served at /static
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")
