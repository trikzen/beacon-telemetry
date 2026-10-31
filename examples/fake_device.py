"""Fake device simulator for beacon-telemetry.

Sends realistic events to the backend so you can test the database, the API
and the dashboard without any hardware. It only uses the Python standard
library, so there is nothing extra to install.

Sensor types (--type):
    motion    state + confidence             (default)
    climate   temperature (C) + humidity (%)
    water     ph + temperature (C) + status
    all       one of each, cycling through the devices
    You can also combine them: --type climate,water

Examples:
    python fake_device.py                                # 10 events from one device
    python fake_device.py --devices 3 --count 20         # 3 devices, 20 rounds each
    python fake_device.py --type climate --devices 2     # temperature + humidity sensors
    python fake_device.py --type all --devices 3 --count 0 --interval 1
    python fake_device.py --rand                         # random types, devices, rounds, interval
    python fake_device.py --rand --devices 3             # random, but always 3 devices
    python fake_device.py --count 0 --interval 0.5       # run until Ctrl+C
    python fake_device.py --invalid-rate 0.2             # 20% deliberately broken events
"""

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

# (state, relative likelihood). Mostly idle, sometimes vibration or tilt.
STATES = [
    ("Idle", 0.60),
    ("Continuous_Vibration", 0.25),
    ("Tilted_Abnormal", 0.15),
]


# Readings drift gradually (a random walk), like a real sensor, instead of jumping around.
def drift(value: float, step: float, low: float, high: float) -> float:
    return min(high, max(low, value + random.uniform(-step, step)))


def motion_reading(state: dict) -> dict:
    name = random.choices(
        [name for name, _ in STATES], weights=[weight for _, weight in STATES]
    )[0]
    return {"state": name, "confidence": round(random.uniform(0.70, 0.99), 2)}


def climate_reading(state: dict) -> dict:
    state.setdefault("temperature", random.uniform(19.0, 25.0))
    state.setdefault("humidity", random.uniform(35.0, 60.0))
    state["temperature"] = drift(state["temperature"], 0.3, 10.0, 35.0)
    state["humidity"] = drift(state["humidity"], 0.8, 20.0, 90.0)
    return {"temperature": round(state["temperature"], 1),
            "humidity": round(state["humidity"], 1)}


def water_reading(state: dict) -> dict:
    state.setdefault("ph", random.uniform(6.8, 7.6))
    state.setdefault("temperature", random.uniform(15.0, 22.0))
    state["ph"] = drift(state["ph"], 0.06, 5.5, 8.8)
    state["temperature"] = drift(state["temperature"], 0.15, 8.0, 30.0)
    ph = round(state["ph"], 2)
    status = "Too_Acidic" if ph < 6.5 else "Too_Alkaline" if ph > 8.0 else "OK"
    return {"ph": ph, "temperature": round(state["temperature"], 1), "status": status}


# type name -> (event_type, device id prefix, function that makes the readings)
SENSOR_TYPES = {
    "motion": ("motion", "esp32_sim", motion_reading),
    "climate": ("climate", "climate", climate_reading),
    "water": ("water_quality", "water", water_reading),
}


def make_event(device_id: str, sequence: int, sensor: str, state: dict) -> dict:
    event_type, _, reading = SENSOR_TYPES[sensor]
    return {
        "schema_version": 1,
        "device_id": device_id,
        "event_type": event_type,
        "sequence": sequence,
        "timestamp": int(time.time()),
        "data": reading(state),
    }


def parse_types(text: str) -> list[str]:
    names = [part.strip().lower() for part in text.split(",") if part.strip()]
    if names == ["all"]:
        names = list(SENSOR_TYPES)
    unknown = [n for n in names if n not in SENSOR_TYPES]
    if unknown or not names:
        raise SystemExit(f"Unknown --type {unknown or text!r}. "
                         f"Choose from: {', '.join(SENSOR_TYPES)}, all")
    return names


def break_event(event: dict) -> dict:
    """Make an event invalid on purpose, to exercise server-side validation."""
    broken = dict(event)
    if random.random() < 0.5:
        del broken["device_id"]
    else:
        broken["sequence"] = -1
    return broken


def post_event(url: str, payload: dict) -> tuple[int, str]:
    """POST one event. Returns (HTTP status, response body)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as err:  # server answered with 4xx/5xx
        return err.code, err.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send fake telemetry events.")
    parser.add_argument("--url", default="http://localhost:8000/events")
    parser.add_argument("--rand", action="store_true",
                        help="pick random settings for anything you don't give "
                             "(types, devices, count, interval)")
    parser.add_argument("--devices", type=int, default=None,
                        help="number of fake devices (default: 1)")
    parser.add_argument("--type", default=None, dest="types",
                        help="sensor type: motion, climate, water, all, or a comma list "
                             "(devices take turns; default: motion)")
    parser.add_argument("--count", type=int, default=None,
                        help="rounds to send (0 = run until Ctrl+C; default: 10)")
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between rounds (default: 1.0)")
    parser.add_argument("--invalid-rate", type=float, default=0.0,
                        help="fraction of events to break on purpose (0 to 1)")
    parser.add_argument("--seed", type=int, default=None,
                        help="random seed, for repeatable runs")
    args = parser.parse_args()

    if args.rand and args.seed is None:
        args.seed = random.randrange(1_000_000)   # printed below, so a run can be repeated
    if args.seed is not None:
        random.seed(args.seed)

    # Anything not given on the command line gets a default, or a random value with --rand.
    if args.rand:
        if args.types is None:
            args.types = ",".join(random.sample(list(SENSOR_TYPES), random.randint(1, 3)))
        if args.devices is None:
            args.devices = random.randint(1, 6)
        if args.count is None:
            args.count = random.randint(20, 60)
        if args.interval is None:
            args.interval = round(random.uniform(0.3, 1.5), 1)
    else:
        args.types = args.types or "motion"
        args.devices = 1 if args.devices is None else args.devices
        args.count = 10 if args.count is None else args.count
        args.interval = 1.0 if args.interval is None else args.interval
    if args.devices < 1:
        raise SystemExit("--devices must be at least 1")
    if args.count < 0:
        raise SystemExit("--count cannot be negative (use 0 to run until Ctrl+C)")
    if args.interval < 0:
        raise SystemExit("--interval cannot be negative")

    types = parse_types(args.types)
    if args.rand:
        print(f"Random run: --type {args.types} --devices {args.devices} "
              f"--count {args.count} --interval {args.interval}")
        print(f"Repeat it exactly with: --rand --seed {args.seed}")
    device_ids = []
    sensor_of = {}                       # device id -> sensor type
    made = {name: 0 for name in types}   # how many devices of each type so far
    for n in range(args.devices):
        sensor = types[n % len(types)]
        made[sensor] += 1
        device_id = f"{SENSOR_TYPES[sensor][1]}_{made[sensor]:02d}"
        device_ids.append(device_id)
        sensor_of[device_id] = sensor
    sequences = {device_id: 1 for device_id in device_ids}
    states = {device_id: {} for device_id in device_ids}   # each device's last readings
    accepted = rejected = 0
    round_number = 0

    print(f"Sending to {args.url} from {len(device_ids)} device(s). Ctrl+C to stop.")
    try:
        while args.count == 0 or round_number < args.count:
            for device_id in device_ids:
                event = make_event(device_id, sequences[device_id],
                                   sensor_of[device_id], states[device_id])
                sequences[device_id] += 1

                deliberately_bad = random.random() < args.invalid_rate
                if deliberately_bad:
                    event = break_event(event)

                try:
                    status, body = post_event(args.url, event)
                except (urllib.error.URLError, OSError) as err:
                    print(f"Could not reach {args.url}: {err}")
                    print("Is the server running? Start it with: "
                          "python -m uvicorn main:app --reload")
                    return 1

                label = " (broken on purpose)" if deliberately_bad else ""
                data = event["data"]
                if status == 200:
                    accepted += 1
                    shown = " ".join(f"{k}={v}" for k, v in data.items())
                    print(f"[{device_id} #{event['sequence']}] "
                          f"{shown} -> accepted{label}")
                else:
                    rejected += 1
                    print(f"[{device_id}] -> rejected with HTTP {status}{label}")

            round_number += 1
            if args.count == 0 or round_number < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")

    print(f"Done: {accepted} accepted, {rejected} rejected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
