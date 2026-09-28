# PackTrack

Camera-driven box tracking for the factory floor. Floor cameras read unique box
barcodes and store calibrated x,y positions in metres. A confirmed sighting inside
a truck camera's collection region marks a box collected. The dashboard shows
floor positions, counts, supplier totals, collection trends and camera evidence.

## Start the interactive prototype

Python 3.10+ (3.12 recommended) and the native zbar library are required. On macOS:

```bash
brew install zbar
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python launch.py
```

Open **http://127.0.0.1:8501**. Keep the terminal running. Ctrl-C stops the API and
dashboard. If ports are occupied:

```bash
.venv/bin/python launch.py --api-port 8010 --dashboard-port 8510
```

Then open **http://127.0.0.1:8510**.

The default mode uses a temporary database and generated QR camera scenes. It
does not open a webcam or change an existing `packtrack.db`.

### Try the workflow

1. The demo places six labelled boxes across two calibrated camera views.
2. Inspect their positions on **Floor overview** and their details in **Box register**.
3. Select a box in **Try a truck pickup** and click **Load into truck**. The demo
   generates a truck-camera frame, decodes its QR, confirms it inside the truck
   region, and records a collection event through the same tracking logic.
4. Watch the floor count decrease and the collection total, supplier chart,
   collection history and camera activity update automatically.
5. **Auto-play pickups** collects one box every 12 seconds. After every box has
   been collected, **New batch** creates fresh unique IDs for another demonstration.

The floor map refreshes every two seconds. It separates coordinate frames and
shows stale positions in amber. Collected positions are hidden by default; enable
**Show collected positions** to see where those boxes were last placed.

## Test QR labels

The **Test QR labels** tab displays six examples and offers downloads. Files are
also included in the project:

- [Printable sheet](assets/qr/test-labels.png)
- [All six individual labels + sheet + payloads](assets/qr/test-labels.zip)
- [QR payload manifest](assets/qr/payloads.json)

IDs are `TEST-001` through `TEST-006`, across three example suppliers. Print one
unique QR per box and preserve its white border. QR payloads contain identity,
supplier and part type; position and collection state come from camera observations.

```json
{"box_id":"TEST-001","supplier":"Acme Components","part":"M6 Bolts"}
```

Generate the files again with `.venv/bin/python tools/make_labels.py`.

## Connect real cameras

Follow [Camera setup and calibration](docs/CAMERAS.md). Replace the synthetic
calibration with measured reference points and configure a region **inside the truck**.
Then launch the entire app and camera runner together:

```bash
.venv/bin/python launch.py --config cameras.json
```

This mode uses your configured cameras and persistent application database. It
hides the demo controls. **Physical coordinates require calibration at the barcode
height.** The current planar method assumes a consistent barcode height; arbitrary
stacking, varying label heights and significant lens distortion require additional
positioning work. The sample coordinates are not measurements of your factory.

## Verify the installation

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/verify_tracking.py
./smoke_test.sh
```

`verify_tracking.py` is an automated command-line check: it prints PASS results
and exits. It does not launch the dashboard. The older `tools/demo_part_a.py`
command remains a compatibility alias for that check. **Use `launch.py` for the app.**

The checks cover calibration, camera regions, confirmation, placement/collection,
late and concurrent events, offline retry, dashboard controls and supplier totals.
Test databases are temporary. Real camera/lens/lighting accuracy still needs field
validation. The optional YOLO check runs only if `requirements-detect.txt` is installed;
the calibrated barcode workflow does not need YOLO.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `PACKTRACK_DB_URL` | `sqlite:///./packtrack.db` | Persistent API database in real-camera mode |
| `PACKTRACK_API_BASE_URL` | `http://127.0.0.1:8000` | API for independently started processes |
| `PACKTRACK_STALE_AFTER` | `60` | Seconds before an uncollected box needs a location check |
| `PACKTRACK_RECONNECT_DELAY` | `3` | Delay between camera reconnection attempts |

Camera source, role, calibration, region, scan interval and confirmation settings
are in the JSON passed to `--config`; see the camera guide. Camera observations
are saved to `camera-outbox.db` before delivery. Retry pending events without
opening cameras using `.venv/bin/python track.py --flush-only`.

## API and data

API docs are at `/docs` on the API port (8000 by default).

| Endpoint | Purpose |
| --- | --- |
| `POST /observations` | Record timestamped camera evidence and update placement/collection |
| `GET /boxes` | Box status, last floor position, supplier and collection camera/time |
| `GET /boxes/{box_id}/observations` | Recent evidence for a box |
| `GET /dashboard` | Counts, supplier breakdown and hourly collection totals |
| `GET /activity` | Recent camera observations and whether they changed state |

Collection is terminal in this prototype; another floor sighting cannot reopen a
collected box. New demo batches use new IDs. Box coordinates are preserved after
collection. Missing floor sightings do not imply collection. Supplier identity
comes from the barcode; the collection camera identifies a loading view, not a
truck registration. Collection charts use UTC.

Older `/scan` and manual status endpoints remain for compatibility but cannot
modify calibrated, camera-tracked boxes. Schema changes are additive. This is a
local prototype without authentication; public deployment is not configured.
