"""Small helpers for reading settings from environment variables."""

import logging
import os

log = logging.getLogger("uvicorn.error")   # shows up next to the server's own messages

TRUE_WORDS = {"1", "true", "yes", "on"}


def env_flag(name: str) -> bool:
    """True if the variable is 1, true, yes or on (any capitals). Unset or anything else is False."""
    return os.environ.get(name, "").strip().lower() in TRUE_WORDS


def env_number(name: str, default: float, low: float, high: float) -> float:
    """A number from the environment, kept between low and high.

    Unset or empty gives the default. Something that is not a number also gives the
    default, with a warning in the log.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    return min(high, max(low, value))
