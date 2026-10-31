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
        # API keys. Only a hash of each key is stored, never the key itself.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_keys (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id   TEXT NOT NULL,
                key_hash    TEXT NOT NULL UNIQUE,
                key_prefix  TEXT NOT NULL,
                created_at  TEXT NOT NULL
                            DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                revoked_at  TEXT
            )
            """
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


def max_sequence(device_id: str) -> int:
    """The highest sequence number stored for a device, or 0 if it has none."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(sequence) FROM events WHERE device_id = ?", (device_id,)
        ).fetchone()
    return row[0] or 0


def prune_events(keep: int, device_ids: list[str]) -> int:
    """Keep only the newest `keep` events of the given devices. Returns how many were deleted.

    Events from any other device are never touched.
    """
    if not device_ids:
        return 0
    marks = ",".join("?" * len(device_ids))
    with get_conn() as conn:
        cur = conn.execute(
            f"""
            DELETE FROM events
            WHERE device_id IN ({marks})
              AND id NOT IN (
                  SELECT id FROM events WHERE device_id IN ({marks})
                  ORDER BY id DESC LIMIT ?
              )
            """,
            [*device_ids, *device_ids, keep],
        )
        return cur.rowcount


# ---- API keys ----
def create_key(device_id: str, key_hash: str, key_prefix: str) -> int:
    """Store the hash of a new key for a device and return the key's id."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO device_keys (device_id, key_hash, key_prefix) VALUES (?, ?, ?)",
            (device_id, key_hash, key_prefix),
        )
        return cur.lastrowid


def find_key(key_hash: str) -> dict | None:
    """The active (not revoked) key with this hash, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, device_id FROM device_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (key_hash,),
        ).fetchone()
    return dict(row) if row else None


def list_keys() -> list[dict]:
    """Every key, oldest first. The hash is never returned."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, device_id, key_prefix, created_at, revoked_at FROM device_keys ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_key(key_id: int) -> dict | None:
    """Revoke a key. Returns the key's details, or None if there is no such key.

    Revoking twice is harmless: the original revoke time is kept.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE device_keys SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND revoked_at IS NULL",
            (key_id,),
        )
        row = conn.execute(
            "SELECT id, device_id, key_prefix, created_at, revoked_at FROM device_keys WHERE id = ?",
            (key_id,),
        ).fetchone()
    return dict(row) if row else None


def count_active_keys() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM device_keys WHERE revoked_at IS NULL"
        ).fetchone()[0]
