#!/usr/bin/env bash
# End-to-end smoke test: boots the API on a scratch DB, renders a test frame,
# runs the pipeline against it, and asserts the data landed correctly.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
PORT=${PORT:-8765}
DB=$(mktemp -d)/smoke.db
export PACKTRACK_DB_URL="sqlite:///${DB}"
export PACKTRACK_API_BASE_URL="http://127.0.0.1:${PORT}"

[ -x "$PY" ] || { echo "FAIL: no venv. Run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

$PY -m uvicorn api:app --host 127.0.0.1 --port "$PORT" --log-level warning &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:${PORT}/dashboard" >/dev/null 2>&1 && break
  sleep 0.25
done

echo "1/6 rendering test frame"
$PY tools/make_test_qr.py --scene --out testdata/scene.png >/dev/null

echo "2/6 running pipeline"
$PY pipeline.py --station STATION-01 --source testdata/scene.png --no-detect 2>&1 | tail -1

echo "3/6 marking B001 empty"
curl -sf -X PATCH "http://127.0.0.1:${PORT}/boxes/B001" \
  -H 'Content-Type: application/json' -d '{"status":"empty"}' >/dev/null

echo "4/6 asserting image pipeline"
$PY - <<'EOF'
import os, sys, requests
base = os.environ["PACKTRACK_API_BASE_URL"]
boxes = requests.get(f"{base}/boxes", timeout=5).json()
stats = requests.get(f"{base}/dashboard", timeout=5).json()
by_id = {b["box_id"]: b for b in boxes}

checks = [
    ("3 boxes logged", len(boxes) == 3),
    ("B001 supplier decoded", by_id.get("B001", {}).get("supplier_name") == "SupplierA"),
    ("B002 part decoded", by_id.get("B002", {}).get("part_type") == "M8 Washer"),
    ("B001 marked empty", by_id.get("B001", {}).get("status") == "empty"),
    ("station recorded", all(b["station_id"] == "STATION-01" for b in boxes)),
    ("dashboard total", stats["total_boxes"] == 3),
    ("pickup queue has SupplierA",
     any(r["supplier"] == "SupplierA" and r["empty_boxes"] == 1 for r in stats["pickup_queue"])),
]
failed = [name for name, ok in checks if not ok]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
if failed:
    sys.exit(1)
EOF

# Runs last: re-sighting these boxes on video moves them to another station,
# which would invalidate the station assertions above.
echo "5/6 sampling a video file"
$PY tools/make_test_qr.py --video --out testdata/station.mp4 >/dev/null
# A 9s clip at a 2s interval must sample a few frames and finish fast -- this
# used to sleep between frames and would have taken minutes.
VIDEO_START=$(date +%s)
PACKTRACK_SCAN_INTERVAL=2 $PY pipeline.py --station STATION-VIDEO \
  --source testdata/station.mp4 --no-detect 2>&1 | tail -1
VIDEO_ELAPSED=$(( $(date +%s) - VIDEO_START ))
if [ "$VIDEO_ELAPSED" -gt 30 ]; then
  echo "  FAIL  video sampling took ${VIDEO_ELAPSED}s (expected well under 30s)"
  exit 1
fi
echo "  PASS  video walked in ${VIDEO_ELAPSED}s"

$PY - <<'EOF'
import os, sys, requests
base = os.environ["PACKTRACK_API_BASE_URL"]
boxes = requests.get(f"{base}/boxes", timeout=5).json()
moved = [b for b in boxes if b["station_id"] == "STATION-VIDEO"]
ok = len(boxes) == 3 and len(moved) >= 2
print(f"  {'PASS' if ok else 'FAIL'}  video re-sighting moved boxes without duplicating "
      f"({len(boxes)} boxes, {len(moved)} at STATION-VIDEO)")
sys.exit(0 if ok else 1)
EOF

echo "6/6 running two stations concurrently from config"
# Both stations decode the same box ids, so the last writer wins in the DB and
# station rows cannot prove both ran. Assert on the pipeline's own log instead.
MULTI_LOG=$(PACKTRACK_CAMERAS="LINE-A=testdata/station.mp4,LINE-B=testdata/scene.png" \
  PACKTRACK_SCAN_INTERVAL=3 $PY pipeline.py --no-detect 2>&1)

MULTI_FAIL=0
for station in LINE-A LINE-B; do
  if printf '%s' "$MULTI_LOG" | grep -q "station ${station}: [0-9]* boxes logged"; then
    echo "  PASS  ${station} captured frames"
  else
    echo "  FAIL  ${station} never logged a frame"
    MULTI_FAIL=1
  fi
done
[ "$MULTI_FAIL" -eq 0 ] || exit 1

echo "SMOKE TEST PASSED"
