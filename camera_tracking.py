"""camera tracking: barcode observations from calibrated floor and truck-interior cameras."""
from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
from uuid import uuid4

import requests

from calibration import Calibration
from decoder import decode_region

log = logging.getLogger(__name__)


@dataclass
class Camera:
    camera_id: str
    source: int | str
    role: str
    roi: tuple[int, int, int, int] | None = None
    calibration: Calibration | None = None
    confirmations: int = 2
    max_motion_px: float = 15.0
    interval_s: float = 0.5

    @classmethod
    def load_all(cls, path):
        path = Path(path)
        data = json.loads(path.read_text())
        cameras, ids = [], set()
        for item in data["cameras"]:
            camera_id = item["camera_id"]
            if not isinstance(camera_id, str) or not camera_id.strip() or camera_id in ids:
                raise ValueError("camera IDs must be nonempty and unique")
            ids.add(camera_id)
            role = item["role"]
            if role not in {"placement", "collection"}:
                raise ValueError("camera role must be placement or collection")
            roi = item.get("roi")
            if roi is not None:
                if len(roi) != 4 or any(type(v) is not int for v in roi) or min(roi[:2]) < 0 or min(roi[2:]) <= 0:
                    raise ValueError("roi must be integer [x, y, width, height] inside the image")
                roi = tuple(roi)
            if role == "collection" and roi is None:
                raise ValueError("collection cameras require an explicit truck-interior roi")
            calibration = Calibration.load(path.parent / item["calibration"]) if item.get("calibration") else None
            if role == "placement" and calibration is None:
                raise ValueError("placement cameras require a measured calibration")
            confirmations = item.get("confirmations", 2)
            interval = float(item.get("interval_s", .5))
            motion = float(item.get("max_motion_px", 15))
            if type(confirmations) is not int or confirmations < 1:
                raise ValueError("confirmations must be a positive integer")
            if not math.isfinite(interval) or interval <= 0 or not math.isfinite(motion) or motion < 0:
                raise ValueError("interval_s must be positive; max_motion_px must be nonnegative")
            source = item["source"]
            if type(source) is not int and not isinstance(source, str):
                raise ValueError("source must be a camera index, URL, or file path")
            if isinstance(source, str) and "://" not in source:
                source = str((path.parent / source).resolve())
            cameras.append(cls(camera_id, source, role, roi, calibration, confirmations, motion, interval))
        if not cameras:
            raise ValueError("configure at least one camera")
        return cameras


@dataclass
class Tracker:
    camera: Camera
    previous: dict = field(default_factory=dict)
    collection_sent: set = field(default_factory=set)

    def reset(self):
        self.previous.clear()
        self.collection_sent.clear()

    def process(self, frame, observed_at=None):
        """Return confirmed evidence; positions are barcode centres, in full-frame pixels."""
        observed_at = observed_at or datetime.now(timezone.utc)
        height, width = frame.shape[:2]
        camera = self.camera
        if camera.calibration and (width, height) != camera.calibration.image_size:
            raise ValueError(f"{camera.camera_id}: resolution differs from calibration")
        rx, ry, rw, rh = camera.roi or (0, 0, width, height)
        if rx < 0 or ry < 0 or rw <= 0 or rh <= 0 or rx + rw > width or ry + rh > height:
            raise ValueError(f"{camera.camera_id}: ROI outside actual image")
        # Decode the entire image so a label cut by an ROI boundary can still
        # be read. Accept only labels completely inside the configured zone.
        hits = {}
        ambiguous = set()
        for decoded in decode_region(frame):
            if not decoded.rect or not decoded.box_id.strip() or "/" in decoded.box_id:
                continue
            x, y, w, h = decoded.rect
            if not (rx <= x and ry <= y and x + w <= rx + rw and y + h <= ry + rh):
                continue
            if decoded.box_id in hits:
                ambiguous.add(decoded.box_id)
            hits[decoded.box_id] = decoded
        current, events = {}, []
        for box_id, decoded in hits.items():
            if box_id in ambiguous:
                log.warning("duplicate physical labels for %s; ignoring ambiguous position", box_id)
                continue
            x, y, w, h = decoded.rect
            px, py = x + w / 2, y + h / 2
            physical = camera.calibration.project(px, py, (width, height)) if camera.calibration else None
            if camera.calibration and physical is None:
                continue
            prev = self.previous.get(box_id)
            stable = prev and math.hypot(px - prev[0], py - prev[1]) <= camera.max_motion_px
            # A long camera interruption cannot count as consecutive evidence.
            if prev and (observed_at - prev[3]).total_seconds() > max(2, camera.interval_s * 3):
                stable = False
            count = prev[2] + 1 if stable else 1
            current[box_id] = (px, py, count, observed_at)
            if count < camera.confirmations or (camera.role == "collection" and box_id in self.collection_sent):
                continue
            event = {
                "event_id": str(uuid4()), "box_id": box_id, "camera_id": camera.camera_id,
                "role": camera.role, "observed_at": observed_at.isoformat(),
                "supplier_name": decoded.supplier, "part_type": decoded.part,
                "pixel_x": px, "pixel_y": py,
            }
            if physical:
                event.update(floor_x=physical[0], floor_y=physical[1],
                             coordinate_frame=camera.calibration.coordinate_frame,
                             calibration_id=camera.calibration.calibration_id)
            events.append(event)
            if camera.role == "collection":
                self.collection_sent.add(box_id)
        self.previous = current
        self.collection_sent.intersection_update(current)
        return events


class Outbox:
    """Persist evidence before HTTP delivery; retry the same event ID after outages."""
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS pending (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def enqueue(self, payload):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO pending VALUES (?, ?)",
                         (payload["event_id"], json.dumps(payload)))

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM pending").fetchone()[0]

    def flush(self, api_base_url):
        with self.connect() as conn:
            rows = conn.execute("SELECT event_id, payload FROM pending ORDER BY rowid LIMIT 100").fetchall()
        sent = 0
        for event_id, body in rows:
            try:
                response = requests.post(f"{api_base_url.rstrip('/')}/observations", json=json.loads(body), timeout=5)
                response.raise_for_status()
            except requests.RequestException as exc:
                log.warning("event %s remains queued: %s", event_id, exc)
                # Retain evidence, including rejected events, for diagnosis.
                if exc.response is None:
                    break
                continue
            with self.connect() as conn:
                conn.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
            sent += 1
        return sent
