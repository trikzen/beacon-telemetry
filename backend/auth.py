"""Device authentication: API keys and a simple rate limit.

How it works:
  * Each key belongs to ONE device id. The device sends it in an X-API-Key header.
  * Only a SHA-256 hash of a key is stored. The key itself is shown once, when it is
    made, and cannot be recovered. A fast hash is fine here (unlike for passwords)
    because keys are long random strings, so there is nothing to guess.
  * Keys can be revoked one at a time.

Settings (environment variables):
    BEACON_REQUIRE_KEY    1 / true / yes / on: events need a valid key  (default: off)
    BEACON_RATE_LIMIT     events per minute allowed per key, 0 = no limit (default: 300)
    BEACON_ADMIN_TOKEN    enables the /admin API; at least 20 characters (default: off)
"""

import hashlib
import math
import os
import secrets
import time
from typing import Callable

import db
from settings import env_flag, env_number

KEY_HEADER = "X-API-Key"
KEY_PREFIX = "bk_"               # makes a key easy to recognise (and to spot in a leak scan)
RESERVED_PREFIX = "demo_"        # device ids the server's own demo devices use
MIN_ADMIN_TOKEN_LENGTH = 20
DEFAULT_RATE_LIMIT = 300         # events per minute per key


# ---- settings ----
def auth_required() -> bool:
    return env_flag("BEACON_REQUIRE_KEY")


def rate_limit() -> int:
    return int(env_number("BEACON_RATE_LIMIT", DEFAULT_RATE_LIMIT, 0, 1_000_000))


def admin_token_setting() -> tuple[str | None, bool]:
    """(token, too_short). The token is None if the admin API is off or the token is too weak."""
    token = os.environ.get("BEACON_ADMIN_TOKEN", "").strip()
    if not token:
        return None, False
    if len(token) < MIN_ADMIN_TOKEN_LENGTH:
        return None, True
    return token, False


# ---- keys ----
def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def clean_device_id(device_id: str) -> str:
    """Check a device id before a key is made for it. Raises ValueError with a plain message."""
    device_id = (device_id or "").strip()
    if not 1 <= len(device_id) <= 64:
        raise ValueError("The device id must be 1 to 64 characters.")
    if device_id.startswith(RESERVED_PREFIX):
        raise ValueError(
            f"Device ids starting with '{RESERVED_PREFIX}' are reserved for the server's "
            "own demo devices. Choose another name."
        )
    return device_id


def issue_key(device_id: str) -> dict:
    """Make a new key for a device. The returned dict is the only time the key is available."""
    device_id = clean_device_id(device_id)
    key = generate_key()
    key_id = db.create_key(device_id, hash_key(key), key[:8])
    return {"id": key_id, "device_id": device_id, "key": key, "prefix": key[:8]}


# ---- rate limit ----
class RateLimiter:
    """Allows at most `limit` events per key in each window (default one minute).

    It lives in memory, so it is per server process and resets when the server restarts.
    Use it from one thread only (the server calls it from the event loop).
    """

    def __init__(self, limit: int, window_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._windows: dict[int, list[float]] = {}   # key id -> [window start, count]

    def check(self, key_id: int) -> int:
        """0 if the event is allowed, otherwise the seconds to wait before trying again."""
        if self.limit <= 0:
            return 0
        now = self._clock()
        window = self._windows.get(key_id)
        if window is None or now - window[0] >= self.window_s:
            self._windows[key_id] = [now, 1]
            return 0
        if window[1] < self.limit:
            window[1] += 1
            return 0
        return max(1, math.ceil(self.window_s - (now - window[0])))
