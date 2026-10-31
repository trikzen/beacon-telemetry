import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import admin
import auth
from db import (count_active_keys, find_key, get_event, get_recent_events, init_db,
                max_sequence, prune_events, save_event)
from demo import demo_enabled, run_demo
from models import Event
from settings import log
from ws_manager import ConnectionManager

BASE_DIR = Path(__file__).resolve().parent
# The dashboard sits next to this folder. Set BEACON_DASHBOARD_DIR to use another location.
DASHBOARD_DIR = Path(os.environ.get("BEACON_DASHBOARD_DIR", BASE_DIR.parent / "dashboard"))
manager = ConnectionManager()
DEMO = demo_enabled()   # BEACON_DEMO=1 makes the server generate its own demo devices
REQUIRE_KEY = auth.auth_required()   # BEACON_REQUIRE_KEY=1: events need a valid API key
limiter = auth.RateLimiter(auth.rate_limit())


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create the tables on startup if they're missing
    if REQUIRE_KEY:
        log.info("Authentication ON: events need an %s header (%d active keys, %s)",
                 auth.KEY_HEADER, await run_in_threadpool(count_active_keys),
                 f"{limiter.limit} events/minute per key" if limiter.limit else "no rate limit")
    else:
        log.warning("Authentication is OFF: anyone who can reach this server can send events. "
                    "Set BEACON_REQUIRE_KEY=1 before exposing it to the internet.")
    if admin.ADMIN_TOKEN:
        log.info("Admin API enabled at /admin (BEACON_ADMIN_TOKEN is set)")
    elif admin.TOKEN_TOO_SHORT:
        log.warning("BEACON_ADMIN_TOKEN is shorter than %d characters, so the admin API is off",
                    auth.MIN_ADMIN_TOKEN_LENGTH)
    demo_task = None
    if DEMO:
        demo_task = asyncio.create_task(run_demo(handle_event, max_sequence, prune_events))
    yield
    if demo_task:
        demo_task.cancel()
        with suppress(asyncio.CancelledError):
            await demo_task


app = FastAPI(title="beacon-telemetry", lifespan=lifespan)
app.include_router(admin.router)


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


async def check_key(x_api_key: str | None = Header(default=None)) -> dict | None:
    """Who is sending? Returns the key's record, or None when authentication is off.

    Runs before the request body is checked, so a stranger learns nothing about the
    event format: without a valid key the answer is always 401.
    """
    if not REQUIRE_KEY:
        return None
    key = (x_api_key or "").strip()
    record = await run_in_threadpool(find_key, auth.hash_key(key)) if key else None
    if record is None:
        # The same answer for "missing", "wrong" and "revoked", so it reveals nothing.
        raise HTTPException(status_code=401, detail="Missing or invalid API key",
                            headers={"WWW-Authenticate": "ApiKey"})
    wait = limiter.check(record["id"])
    if wait:
        raise HTTPException(status_code=429, detail="Too many events from this key",
                            headers={"Retry-After": str(wait)})
    return record


@app.get("/", include_in_schema=False)
def dashboard():
    """The dashboard page: http://localhost:8000"""
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "message": "telemetry server running", "demo": DEMO,
            "auth_required": REQUIRE_KEY}


@app.post("/events")
async def receive_event(event: Event, key: dict | None = Depends(check_key)):
    # A key only works for the device it was made for.
    if key is not None and key["device_id"] != event.device_id:
        raise HTTPException(status_code=403, detail="This API key does not belong to that device_id")
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
