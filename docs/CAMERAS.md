# Camera setup and calibration

Camera tracking runs independently of Streamlit. It uses barcode/QR identity and fixed,
configured camera zones; it does not require YOLO or manual status changes.

```text
Floor camera → decode label → confirm stable sighting → calibrated x,y metres → placed
Truck camera → decode label inside truck ROI → confirm stable sighting → collected
```

## Verify without hardware

Install the README requirements, then run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/verify_tracking.py
```

The demo starts a temporary API/database on port 8771, generates a short video,
and runs the real `track.py` command against floor and truck configurations.
It verifies three placements, collection of only the box inside the truck zone,
and preservation of the collection state when the floor camera sees it again.
Use `--port 8772` if needed. All coordinates in this demo use **synthetic calibration**;
they are not measurements of your factory. It exits and cleans up automatically.

## Physical floor coordinates: what is required

A pixel coordinate alone is not a physical position. Each fixed floor camera
needs four measured reference points, a common factory origin and axes, and a
calibration for its exact resolution and lens/zoom/pose.

This prototype uses a **planar homography of the barcode-centre plane**. All tracked
barcode centres and all four calibration targets must lie on the same horizontal
plane. The saved x,y is the barcode centre's vertical projection onto factory
floor axes, in metres; it is not necessarily the centre or footprint of the box.

- For labels at a fixed height (for example 1 m), place reference targets at that
  same height and measure the floor x,y directly beneath each target.
- Calibrating with floor targets at height 0 and tracking raised labels will produce
  incorrect floor positions. `plane_height_m` records this setup; it does **not**
  compensate for unknown or varying label heights.
- If labels vary in height, boxes are stacked, or cameras move, this method is not
  sufficient: use depth/stereo, known 3D marker geometry, or a separate calibrated
  ground-contact detector. Lens distortion must be negligible or corrected before
  calibration and capture. No physical accuracy claim is made without field checks.

Choose four points surrounding the operating area, in perimeter order. Corresponding
pixel and metre points must have the same order. Coordinates outside that quadrilateral
are rejected rather than extrapolated. Verify the calibration against additional
measured points that were not used to create it.

### Create the calibration

Save a reference frame from the fixed camera. Read the pixel locations of your four
reference targets using an image editor. Supply the corresponding measured x,y values:

```bash
.venv/bin/python tools/calibrate_camera.py \
  --image floor-reference.png \
  --image-points '[[100,100],[1100,120],[1050,650],[150,620]]' \
  --floor-points '[[0,0],[10,0],[10,6],[0,6]]' \
  --plane-height-m 1.0 \
  --coordinate-frame factory-floor \
  --out floor-a.json
```

Those numbers are only an example: replace them with your measurements. The tool
validates the quadrilaterals and generates a calibration ID. It refuses to overwrite
an existing calibration file. Keep old files so historical coordinates remain
traceable. Recalibrate after changing camera pose, focus/zoom, reference plane or resolution.
Use the same `coordinate-frame`, origin, axes and metre units for multiple floor cameras.

### Configure cameras

Create `cameras.json` alongside `floor-a.json`:

```json
{
  "cameras": [
    {
      "camera_id": "FLOOR-A",
      "source": 0,
      "role": "placement",
      "calibration": "floor-a.json",
      "interval_s": 0.5,
      "confirmations": 2,
      "max_motion_px": 15
    },
    {
      "camera_id": "TRUCK-01",
      "source": "rtsp://camera-address/stream",
      "role": "collection",
      "roi": [100, 100, 1000, 500],
      "interval_s": 0.5,
      "confirmations": 2,
      "max_motion_px": 15
    }
  ]
}
```

- `source`: integer USB index, RTSP/HTTP URL, or video/still-image path. Relative
  image/video and calibration paths resolve against the config file directory.
- `role`: `placement` requires a floor calibration; `collection` requires an explicit
  truck-interior `roi` rectangle `[left, top, width, height]` in pixels. Only barcode
  bounds fully inside it qualify. Put this region **inside the truck bed**, not on
  the approach path. Adjust it if the truck/camera moves.
- `confirmations`: consecutive sampled sightings before accepting an observation.
  Default 2. A still image yields one frame, so use 1 only for still-image tests.
- `max_motion_px`: maximum movement between consecutive label centres, default 15.
  This is a simple stability check, not proof that a box has been put down. Tune
  together with the sampling interval and confirmation count on real footage.
- A missing barcode, large position jump, long observation gap or reported camera
  outage resets confirmation. Duplicate visible labels with the same box ID are
  treated as ambiguous and skipped. Every physical box needs a unique barcode.
- A collection event is emitted once per continuous confirmed appearance. A new
  appearance may produce another event, but it does not count the box twice.

The file in `examples/` is a template using fake calibration. Do not use its
coordinates as real measurements.

### Start camera tracking

Start the API, then the camera runner in a second terminal:

```bash
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000
.venv/bin/python track.py --config cameras.json
```

`PACKTRACK_API_BASE_URL` changes the API destination. `PACKTRACK_DB_URL` changes
the API's database. The runner's JSON is independent of the old `PACKTRACK_CAMERAS`
configuration used by `pipeline.py`. Ctrl-C stops the runner. Failed camera opens
are retried; invalid calibration/ROI stops that camera and logs the error.

Camera observations first enter a persistent SQLite outbox, `camera-outbox.db`,
and are sent to the API in the background. API/network failures retain the same
event IDs, so retrying cannot duplicate accepted evidence. To retry queued events
without opening cameras:

```bash
.venv/bin/python track.py --flush-only --outbox camera-outbox.db
```

Do not delete the outbox while events are pending. HTTP validation errors stay queued
and are logged for diagnosis. Keep camera-machine clocks synchronized. Event times
refer to capture/read time on the worker; replayed files use replay time, not the
original recording timestamp. Delivery retries may arrive later than capture.

## Stored state and evidence

`GET /boxes` includes these fields for each box:

| Field | Meaning |
| --- | --- |
| `box_id` | Unique decoded barcode identity |
| `status` | `placed` or `collected` for the camera tracking flow |
| `station_id` | Most recent placement camera; for an unseen box first observed in a truck, the collection camera |
| `floor_x`, `floor_y` | Last measured floor x,y in metres, preserved after collection |
| `coordinate_frame` | Factory origin/axes identifier |
| `calibration_id` | Calibration that produced the last floor position |
| `placed_at` | Time of the latest accepted placement observation |
| `collected_at` | Time collection was first confirmed |
| `collection_camera_id` | Truck camera that confirmed collection |

`POST /observations` accepts the runner's timestamped evidence. Placement requires
physical coordinates and calibration metadata. `GET /boxes/{box_id}/observations`
returns recent evidence, including camera, pixel centre, physical coordinates when
available, timestamps, and whether the observation changed state. The default limit
is 100; `?limit=1000` retrieves up to 1000 recent records.

Collection is terminal for this camera tracking prototype. Later floor sightings cannot reopen
it, and delayed older placement events cannot move a box back to an old location.
Concurrent writers use version checks. An unknown box seen in a truck is recorded as
collected with unknown floor coordinates. The old `/scan` and manual status endpoint
cannot overwrite camera-tracked boxes. A future reuse workflow should explicitly
start a new box cycle; it is not inferred from another barcode sighting.

Disappearance from a floor camera alone never means collection. Truck-camera evidence
is required. The system does not infer supplier identity or vehicle identity from
appearance; supplier metadata comes from the barcode, and `collection_camera_id`
identifies the configured loading camera, not a truck number.

## Field validation

USB/RTSP hardware, lighting, label size/visibility, fixed label height, lens distortion,
truck ROI placement and confirmation thresholds must be validated on the actual floor.
A camera that cannot see a barcode cannot identify that box. Collection ROI confirmation
is a configured-zone heuristic, not a general 3D "inside truck" detector. Camera backend
hangs and reconnect behaviour need hardware testing. The service is a local prototype
without authentication. The dashboard displays physical floor positions, supplier totals, collections and recent camera activity.
