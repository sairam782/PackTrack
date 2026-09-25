# PackTrack — Factory Box Tracker

Tracks empty packaging boxes on a factory floor. Fixed cameras watch each
station; the pipeline detects box regions, decodes the QR/barcode on each one,
and logs the box's location, type and supplier so logistics knows what to
collect and from where.

```
Camera / video / still image
  -> capture.py    grab a frame on an interval
  -> detector.py   YOLOv8 finds box regions          (optional, see Limitations)
  -> decoder.py    pyzbar reads the QR in each crop
  -> api.py        POST /scan logs id, station, supplier, bbox
  -> dashboard.py  box locations + supplier pickup queue
```

## Requirements

- **Python 3.10+** (3.12 recommended). The code uses `X | None` annotations and
  builtin generics, so 3.9 will not run it.
- **zbar**, the native library `pyzbar` binds to. Without it `pyzbar` fails at
  import:

```bash
brew install zbar
```

On Apple Silicon, note that an x86_64 Anaconda will not work as the interpreter
unless Rosetta is installed — use a native Python.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Verify it works

```bash
./smoke_test.sh
```

Boots the API against a scratch database, renders test frames, runs the
pipeline, and asserts the full decode → POST → query → aggregate chain. The
YOLO step is skipped automatically if the detection extras are not installed.

## Running it

Three processes. The API first:

```bash
.venv/bin/python -m uvicorn api:app --port 8000
```

The dashboard (http://127.0.0.1:8501):

```bash
.venv/bin/python -m streamlit run dashboard.py
```

The capture pipeline. Generate a test frame first if you have no camera:

```bash
.venv/bin/python tools/make_test_qr.py --scene
.venv/bin/python pipeline.py --source testdata/scene.png --no-detect
```

### Pipeline options

| Flag | Meaning |
| --- | --- |
| `--source` | USB index (`0`), RTSP URL, or path to a video or still image |
| `--station` | Run one station instead of every configured one |
| `--no-detect` | Skip YOLO and decode whole frames |
| `--once` | Process a single frame and exit |

With no `--source`, every station in `PACKTRACK_CAMERAS` runs concurrently in
one process.

## Configuration

All settings are environment variables, read in `config.py`.

| Variable | Default | Notes |
| --- | --- | --- |
| `PACKTRACK_CAMERAS` | `STATION-01=0` | `STATION=source` pairs, or JSON. Sources keep their type, so USB indices, file paths and RTSP URLs all work. |
| `PACKTRACK_SCAN_INTERVAL` | `5.0` | Seconds between frames. For video files this means seconds of *footage*. |
| `PACKTRACK_DB_URL` | `sqlite:///./packtrack.db` | Any SQLAlchemy URL. |
| `PACKTRACK_API_BASE_URL` | `http://127.0.0.1:8000` | Where the pipeline and dashboard post/read. |
| `PACKTRACK_BOX_CLASSES` | `73` | Detector class filter, or `all`. See Limitations. |
| `PACKTRACK_YOLO_WEIGHTS` | `yolov8n.pt` | Path to a custom-trained model once one exists. |

Several cameras at once:

```bash
export PACKTRACK_CAMERAS="LINE-A=rtsp://cam1.local/s1,LINE-B=rtsp://cam2.local/s1,LINE-C=0"
```

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /scan` | Log a decoded box. Upserts: a re-sighting bumps `last_seen` and does not overwrite a manually set status. |
| `GET /boxes` | List boxes, filterable by `station`, `supplier`, `status`. |
| `PATCH /boxes/{box_id}` | Update status (`active` / `empty` / `collected`). |
| `GET /dashboard` | Totals, per-station counts, supplier pickup queue. |

Interactive docs at `/docs` while the API is running.

## Optional: YOLO detection

```bash
.venv/bin/pip install -r requirements-detect.txt
```

Roughly 2 GB, because it pulls PyTorch — which is why it is kept out of the
core requirements. Without it the pipeline decodes whole frames, which is the
path that actually works today.

## Limitations

These are known and deliberate for a prototype:

- **Detection quality is unproven.** The default class filter is COCO 73
  ("book"), the nearest rectangular stand-in — COCO has no cardboard-box class.
  It reliably finds nothing on real packaging. The `detect → crop → decode`
  wiring is tested and correct, but useful detection needs a model trained on
  labeled box images. Until then, run with `--no-detect`.
- **RTSP and USB webcams are untested.** Both share a code path that is
  exercised only by file-based sources. A webcam additionally needs macOS
  camera permission granted to your terminal (System Settings → Privacy &
  Security → Camera).
- **A box seen at two stations keeps the most recent sighting.** Fine while
  each box lives at one station; revisit if boxes are tracked in transit.
- **No auth, rate limiting, or deployment config**, and a single process
  throughout — per the prototype scope.
