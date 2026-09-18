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

echo "1/4 rendering test frame"
$PY tools/make_test_qr.py --scene --out testdata/scene.png >/dev/null

echo "2/4 running pipeline"
$PY pipeline.py --station STATION-01 --source testdata/scene.png --no-detect 2>&1 | tail -1

echo "3/4 marking B001 empty"
curl -sf -X PATCH "http://127.0.0.1:${PORT}/boxes/B001" \
  -H 'Content-Type: application/json' -d '{"status":"empty"}' >/dev/null

echo "4/4 asserting"
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

echo "SMOKE TEST PASSED"
