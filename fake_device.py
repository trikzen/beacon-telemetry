"""Fake device simulator for beacon-telemetry.

Sends realistic motion events to the backend so you can test the database,
the API and (later) the dashboard without any hardware. It only uses the
Python standard library, so there is nothing extra to install.

Examples:
    python fake_device.py                                # 10 events from one device
    python fake_device.py --devices 3 --count 20         # 3 devices, 20 rounds each
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


def make_event(device_id: str, sequence: int) -> dict:
    state = random.choices(
        [name for name, _ in STATES], weights=[weight for _, weight in STATES]
    )[0]
    return {
        "schema_version": 1,
        "device_id": device_id,
        "event_type": "motion",
        "sequence": sequence,
        "timestamp": int(time.time()),
        "data": {
            "state": state,
            "confidence": round(random.uniform(0.70, 0.99), 2),
        },
    }


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
    parser.add_argument("--devices", type=int, default=1, help="number of fake devices")
    parser.add_argument("--count", type=int, default=10,
                        help="rounds to send (0 = run until Ctrl+C)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between rounds")
    parser.add_argument("--invalid-rate", type=float, default=0.0,
                        help="fraction of events to break on purpose (0 to 1)")
    parser.add_argument("--seed", type=int, default=None,
                        help="random seed, for repeatable runs")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    device_ids = [f"esp32_sim_{n:02d}" for n in range(1, args.devices + 1)]
    sequences = {device_id: 1 for device_id in device_ids}
    accepted = rejected = 0
    round_number = 0

    print(f"Sending to {args.url} from {len(device_ids)} device(s). Ctrl+C to stop.")
    try:
        while args.count == 0 or round_number < args.count:
            for device_id in device_ids:
                event = make_event(device_id, sequences[device_id])
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
                    print(f"[{device_id} #{event['sequence']}] "
                          f"{data['state']} {data['confidence']} -> accepted{label}")
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
