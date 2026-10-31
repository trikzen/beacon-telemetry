"""Demo mode: the server makes up its own devices and readings.

Switched on with the environment variable BEACON_DEMO=1 (off by default).
It exists so a public link always shows live charts without anyone having to
run a simulator. The fake devices are named demo_*, and their events go through
the same handle_event() path as real devices: validated, saved and broadcast.

Settings (environment variables):
    BEACON_DEMO           1 / true / yes / on to enable          (default: off)
    BEACON_DEMO_INTERVAL  seconds between ticks, 0.2 to 60       (default: 1.0)
    BEACON_DEMO_KEEP      newest demo events to keep, min 100    (default: 3000)
"""

import asyncio
import random
import time
from typing import Awaitable, Callable

from models import Event
from settings import env_flag, env_number, log

DEFAULT_INTERVAL = 1.0
DEFAULT_KEEP = 3000
PRUNE_EVERY_S = 60


# ---- settings ----
def demo_enabled() -> bool:
    return env_flag("BEACON_DEMO")


def demo_interval() -> float:
    return env_number("BEACON_DEMO_INTERVAL", DEFAULT_INTERVAL, 0.2, 60.0)


def demo_keep() -> int:
    return int(env_number("BEACON_DEMO_KEEP", DEFAULT_KEEP, 100, 1_000_000))


# ---- fake readings ----
# Each reading changes a little from the last one, like a real sensor, instead of jumping.
def _drift(value: float, pull_to: float, pull: float, step: float) -> float:
    return value + (pull_to - value) * pull + random.uniform(-step, step)


# state -> (chance of staying in it, where it goes otherwise)
MOTION = {
    "Idle": (0.93, ["Continuous_Vibration", "Continuous_Vibration", "Tilted_Abnormal"]),
    "Continuous_Vibration": (0.85, ["Idle"]),
    "Tilted_Abnormal": (0.80, ["Idle"]),
}


def motion_reading(s: dict) -> dict:
    state = s.setdefault("state", "Idle")
    stay, others = MOTION[state]
    if random.random() > stay:
        state = random.choice(others)
    s["state"] = state
    return {"state": state, "confidence": round(random.uniform(0.70, 0.99), 2)}


def climate_reading(s: dict) -> dict:
    t = s.setdefault("temperature", random.uniform(19.0, 24.0))
    h = s.setdefault("humidity", random.uniform(40.0, 55.0))
    new_t = min(35.0, max(10.0, _drift(t, 21.5, 0.01, 0.25)))
    # humidity moves the opposite way to temperature, as it does in a real room
    new_h = min(90.0, max(20.0, _drift(h, 48.0, 0.02, 0.5) - (new_t - t) * 1.5))
    s["temperature"], s["humidity"] = new_t, new_h
    return {"temperature": round(new_t, 1), "humidity": round(new_h, 1)}


def water_reading(s: dict) -> dict:
    ph = s.setdefault("ph", random.uniform(6.9, 7.5))
    temp = s.setdefault("temperature", random.uniform(15.0, 21.0))
    ph = _drift(ph, 7.2, 0.03, 0.05)
    if random.random() < 0.004:              # now and then, a swing that triggers a warning
        ph += random.choice([-1, 1]) * random.uniform(0.7, 1.0)
    ph = min(8.8, max(5.5, ph))
    temp = min(30.0, max(8.0, _drift(temp, 17.5, 0.01, 0.12)))
    s["ph"], s["temperature"] = ph, temp
    ph = round(ph, 2)
    status = "Too_Acidic" if ph < 6.5 else "Too_Alkaline" if ph > 8.0 else "OK"
    return {"ph": ph, "temperature": round(temp, 1), "status": status}


# device id, event type, reading function, ticks between readings
DEMO_DEVICES = [
    ("demo_motion_01", "motion", motion_reading, 1),
    ("demo_motion_02", "motion", motion_reading, 1),
    ("demo_climate_01", "climate", climate_reading, 2),
    ("demo_climate_02", "climate", climate_reading, 2),
    ("demo_water_01", "water_quality", water_reading, 3),
    ("demo_water_02", "water_quality", water_reading, 3),
]


# ---- the loop ----
async def run_demo(
    handle: Callable[[Event], Awaitable[dict]],
    last_sequence: Callable[[str], int],
    prune: Callable[[int, list[str]], int],
) -> None:
    """Make up readings forever. The caller cancels this task on shutdown.

    The three callables come from main.py, so this file never imports the app.
    """
    interval, keep = demo_interval(), demo_keep()
    log.info("Demo mode ON: %d simulated devices, a tick every %.1fs, keeping the newest %d demo events",
             len(DEMO_DEVICES), interval, keep)

    # Carry on from each device's last sequence number, so restarts never repeat one.
    next_seq = {}
    for device_id, *_ in DEMO_DEVICES:
        next_seq[device_id] = await asyncio.to_thread(last_sequence, device_id) + 1
    state = {device_id: {} for device_id, *_ in DEMO_DEVICES}

    tick = 0
    last_prune = time.monotonic()
    due = time.monotonic()
    while True:
        try:
            for device_id, event_type, reading, every in DEMO_DEVICES:
                if tick % every:
                    continue
                event = Event(
                    device_id=device_id,
                    event_type=event_type,
                    sequence=next_seq[device_id],
                    timestamp=int(time.time()),
                    data=reading(state[device_id]),
                )
                await handle(event)
                next_seq[device_id] += 1
            if time.monotonic() - last_prune >= PRUNE_EVERY_S:
                last_prune = time.monotonic()
                await asyncio.to_thread(prune, keep, [d[0] for d in DEMO_DEVICES])
        except asyncio.CancelledError:
            raise
        except Exception:
            # One bad tick (say, a locked database) must not stop the demo for good.
            log.exception("Demo mode: a tick failed; carrying on")
        tick += 1
        due += interval
        await asyncio.sleep(max(0.0, due - time.monotonic()))
