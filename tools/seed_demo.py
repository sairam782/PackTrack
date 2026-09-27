"""Reset the database and replay both demo clips through the API.

One command to get a populated dashboard, so a demo can be reset between runs.

    python tools/seed_demo.py                  # against http://127.0.0.1:8000
    python tools/seed_demo.py --base-url ...
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (clip, station, how long ago its crossings should look)
PLAN = [
    ("inbound.mp4", "STATION-IN", 180.0),
    ("outbound.mp4", "STATION-OUT", 25.0),
]


def wait_for(base: str, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = requests.get(f"{base}/api/jobs/{job_id}", timeout=10).json()
        if snapshot["status"] != "running":
            return snapshot
        print(f"    {snapshot['percent']:5.1f}%  {snapshot['detail']}", end="\r")
        time.sleep(0.5)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--keep", action="store_true", help="do not clear existing data first")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    try:
        health = requests.get(f"{base}/api/health", timeout=5).json()
    except requests.RequestException as exc:
        print(f"API unreachable at {base}: {exc}", file=sys.stderr)
        print("Start it first:  python api.py", file=sys.stderr)
        return 1

    print(f"API {health['version']} — QR backend: {health['qr_backend']}")
    if health["qr_backend"] != "pyzbar":
        print(f"  note: {health['qr_backend_note']}")

    if not health["demo_clips"]:
        print("No demo clips. Run:  python tools/make_demo_video.py", file=sys.stderr)
        return 1

    if not args.keep:
        requests.post(f"{base}/api/demo/reset", timeout=10).raise_for_status()
        print("cleared existing boxes and crossings")

    for clip, station, minutes_ago in PLAN:
        if clip not in health["demo_clips"]:
            print(f"  skipping {clip}: not present")
            continue
        print(f"  reading {clip} at {station}...")
        created = requests.post(
            f"{base}/api/video/demo",
            params={"clip": clip, "station_id": station, "offset_minutes": minutes_ago},
            timeout=10,
        )
        created.raise_for_status()
        done = wait_for(base, created.json()["job_id"])
        if done["status"] != "done":
            print(f"    failed: {done.get('error')}", file=sys.stderr)
            return 1
        result = done["result"]
        print(
            f"    {result['events']} crossings from {result['scans']} reads "
            f"({result['suppressed']} repeats collapsed)        "
        )

    kpis = requests.get(f"{base}/api/dashboard", timeout=10).json()["kpis"]
    print(
        f"\nready: {kpis['total_boxes']} boxes, {kpis['in_stock']} on the floor, "
        f"{kpis['departed']} departed"
    )
    print(f"open {base}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
