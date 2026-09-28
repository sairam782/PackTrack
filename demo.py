"""Synthetic camera scenario for the interactive prototype; enabled only in demo mode."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading

from calibration import Calibration
from camera_tracking import Camera, Tracker
from db import Box, SessionLocal
from observations import ObservationIn, observe
from tools.make_labels import LABELS
from tools.make_test_qr import build_scene


class DemoWorld:
    def __init__(self):
        self.lock = threading.RLock()
        self.specs = deepcopy(LABELS)
        self.batch = 1
        self.auto_collect = False
        self.last_collection = datetime.now(timezone.utc)
        self.trackers = {}
        for index, station in enumerate(("FLOOR-A", "FLOOR-B")):
            offset = index * 10
            calibration = Calibration.from_dict({
                "image_size": [1280, 720], "image_points": [[0, 0], [1279, 0], [1279, 719], [0, 719]],
                "floor_points": [[offset, 0], [offset + 12.79, 0], [offset + 12.79, 7.19], [offset, 7.19]],
                "coordinate_frame": "demo-factory-floor", "units": "metres", "plane_height_m": 1.0,
            })
            self.trackers[station] = Tracker(Camera(station, 0, "placement", calibration=calibration))
        self.truck = Tracker(Camera("TRUCK-01", 1, "collection", roi=(0, 200, 300, 350)))

    def _submit(self, events):
        with SessionLocal() as session:
            for event in events:
                observe(ObservationIn(**event), session)

    def tick(self):
        with self.lock:
            with SessionLocal() as session:
                available = {b.box_id for b in session.query(Box).all() if b.status != "collected"}
                existing = {b.box_id for b in session.query(Box).all()}
            for index, station in enumerate(self.trackers):
                specs = [p for i, p in enumerate(self.specs) if i % 2 == index]
                # Each scene has three stable storage slots. Additional demo
                # batches replace already collected boxes, not current ones.
                frame = build_scene(specs[:3])
                for slot, spec in enumerate(specs[:3]):
                    if spec["box_id"] in existing and spec["box_id"] not in available:
                        x = 40 + slot * 300
                        frame[230:590, x - 20:x + 260] = 110
                self._submit(self.trackers[station].process(frame))
            if self.auto_collect and (datetime.now(timezone.utc) - self.last_collection).total_seconds() >= 12:
                if available:
                    self.collect(sorted(available)[0])
                self.last_collection = datetime.now(timezone.utc)

    def collect(self, box_id):
        with self.lock:
            spec = next((p for p in self.specs if p["box_id"] == box_id), None)
            with SessionLocal() as session:
                box = session.get(Box, box_id)
                if spec is None or box is None or box.status == "collected":
                    raise ValueError("Choose a box currently on the floor")
            self.truck.reset()
            frame = build_scene([spec])
            self.truck.process(frame)
            events = self.truck.process(frame)
            self._submit(events)
            self.last_collection = datetime.now(timezone.utc)
            return {"box_id": box_id, "status": "collected", "camera_id": "TRUCK-01"}

    def add_batch(self):
        with self.lock:
            with SessionLocal() as session:
                remaining = session.query(Box).filter(Box.status != "collected").count()
            if remaining:
                raise ValueError("Collect the current boxes before starting a new batch")
            self.batch += 1
            self.specs = [{**p, "box_id": f"RUN{self.batch}-{p['box_id']}"} for p in LABELS]
            for tracker in self.trackers.values():
                tracker.reset()
            self.tick()
            self.tick()
            return {"batch": self.batch}
