"""All database code lives here, so the rest of the app never touches SQL.

To swap SQLite for another database later, only this file needs to change.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import timezone

from models import Event

DB_PATH = os.environ.get("BEACON_DB", "telemetry.db")


@contextmanager
def get_conn():
    """Open a connection, commit on success, roll back on error, always close."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the events table if it doesn't exist yet."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version INTEGER NOT NULL,
                device_id      TEXT    NOT NULL,
                event_type     TEXT    NOT NULL,
                sequence       INTEGER NOT NULL,
                timestamp      TEXT    NOT NULL,
                data           TEXT    NOT NULL,
                received_at    TEXT    NOT NULL
                               DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_device ON events (device_id, id)"
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["data"] = json.loads(item["data"])
    return item


def save_event(event: Event) -> int:
    """Store one validated event and return its new row id."""
    ts = event.timestamp
    if ts.tzinfo is None:  # no timezone given: assume UTC
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO events
                (schema_version, device_id, event_type, sequence, timestamp, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.schema_version,
                event.device_id,
                event.event_type,
                event.sequence,
                ts.isoformat(),
                json.dumps(event.data),
            ),
        )
        return cur.lastrowid


def get_event(event_id: int) -> dict | None:
    """Return one stored event by id, or None if it doesn't exist."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_recent_events(limit: int = 50, device_id: str | None = None) -> list[dict]:
    """Return the most recently received events, newest first."""
    query = "SELECT * FROM events"
    params: list = []
    if device_id is not None:
        query += " WHERE device_id = ?"
        params.append(device_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_dict(row) for row in rows]
