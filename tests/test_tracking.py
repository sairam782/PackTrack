"""Physical calibration, camera confirmation, delivery and lifecycle regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import numpy as np
import requests
from fastapi import HTTPException
from pydantic import ValidationError

import test_workflow as workflow
from calibration import Calibration
from camera_tracking import Camera, Outbox, Tracker
from db import Observation
from observations import ObservationIn, observe
from tools.make_test_qr import build_scene, DEFAULT_BOXES

ROOT = Path(__file__).resolve().parents[1]


def calibration():
    return Calibration.load(ROOT / "examples/floor-calibration.synthetic.json")


class CameraGeometryTests(unittest.TestCase):
    def test_physical_metres_and_confirmed_barcode_centres(self):
        tracker = Tracker(Camera("FLOOR", 0, "placement", calibration=calibration()))
        frame = build_scene(DEFAULT_BOXES)
        self.assertEqual(tracker.process(frame), [])
        events = tracker.process(frame)
        self.assertEqual(len(events), 3)
        b1 = next(e for e in events if e["box_id"] == "B001")
        self.assertAlmostEqual(b1["floor_x"], 1.5, delta=.03)
        self.assertAlmostEqual(b1["floor_y"], 3.6, delta=.03)
        self.assertEqual(b1["coordinate_frame"], "synthetic-factory-floor")
        self.assertEqual(b1["calibration_id"], calibration().calibration_id)

    def test_collection_only_inside_truck_roi_and_once_per_entry(self):
        tracker = Tracker(Camera("TRUCK", 1, "collection", roi=(0, 200, 300, 350)))
        frame = build_scene(DEFAULT_BOXES)
        self.assertEqual(tracker.process(frame), [])
        self.assertEqual([e["box_id"] for e in tracker.process(frame)], ["B001"])
        self.assertEqual(tracker.process(frame), [])
        tracker.process(np.full_like(frame, 100))
        self.assertEqual(tracker.process(frame), [])
        self.assertEqual([e["box_id"] for e in tracker.process(frame)], ["B001"])

    def test_missing_frame_or_motion_resets_confirmation(self):
        tracker = Tracker(Camera("FLOOR", 0, "placement", calibration=calibration()))
        frame = build_scene(DEFAULT_BOXES[:1])
        tracker.process(frame)
        moved = np.roll(frame, 100, axis=1)
        self.assertEqual(tracker.process(moved), [])
        tracker.reset()
        self.assertEqual(tracker.process(moved), [])
        self.assertEqual(len(tracker.process(moved)), 1)

    def test_calibration_rejects_resolution_change_and_extrapolation(self):
        model = calibration()
        self.assertIsNone(model.project(-1, 100, (1280, 720)))
        with self.assertRaises(ValueError):
            model.project(100, 100, (640, 360))
        with self.assertRaises(ValueError):
            Tracker(Camera("TRUCK", 0, "collection", roi=(0, 0, 1400, 720))).process(build_scene(DEFAULT_BOXES))

    def test_perspective_mapping_and_degenerate_calibration(self):
        model = Calibration.from_dict({
            "image_size": [1000, 1000], "image_points": [[100, 100], [800, 150], [700, 800], [200, 700]],
            "floor_points": [[0, 0], [5, 0], [5, 4], [0, 4]],
            "coordinate_frame": "floor", "plane_height_m": 1, "units": "metres",
        })
        np.testing.assert_allclose(model.project(700, 800, (1000, 1000)), [5, 4], atol=1e-6)
        with self.assertRaises(ValueError):
            Calibration.from_dict({"image_size": [10, 10], "image_points": [[1, 1]] * 4,
                                   "floor_points": [[1, 1]] * 4, "coordinate_frame": "x",
                                   "plane_height_m": 1, "units": "metres"})

    def test_outbox_survives_restart_and_retries_same_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.db"
            event = {"event_id": str(uuid4()), "box_id": "B1"}
            outbox = Outbox(path)
            outbox.enqueue(event)
            with patch("requests.post", side_effect=requests.ConnectionError("offline")):
                self.assertEqual(outbox.flush("http://local"), 0)
            recovered = Outbox(path)
            self.assertEqual(recovered.count(), 1)
            with patch("requests.post", return_value=Mock()) as post:
                self.assertEqual(recovered.flush("http://local"), 1)
                self.assertEqual(post.call_args.kwargs["json"]["event_id"], event["event_id"])
            self.assertEqual(recovered.count(), 0)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        workflow.Base.metadata.drop_all(workflow.engine)
        workflow.init_db()
        self.session = workflow.SessionLocal()
        self.addCleanup(self.session.close)
        self.time = datetime.now(timezone.utc) - timedelta(seconds=10)

    def payload(self, role="placement", seconds=0, **overrides):
        data = dict(event_id=str(uuid4()), box_id="B1", camera_id="FLOOR" if role == "placement" else "TRUCK",
                    role=role, observed_at=self.time + timedelta(seconds=seconds), pixel_x=150, pixel_y=360)
        if role == "placement":
            data.update(floor_x=1.5, floor_y=3.6, coordinate_frame="factory-floor", calibration_id="cal-1")
        data.update(overrides)
        return ObservationIn(**data)

    def test_automatic_placement_then_collection_preserves_floor_position(self):
        observe(self.payload(), self.session)
        box = self.session.get(workflow.Box, "B1")
        self.assertEqual(box.status, "placed")
        self.assertEqual((box.floor_x, box.floor_y), (1.5, 3.6))
        observe(self.payload("collection", 2), self.session)
        self.assertEqual(box.status, "collected")
        self.assertEqual(box.station_id, "FLOOR")
        self.assertEqual(box.collection_camera_id, "TRUCK")
        self.assertEqual((box.floor_x, box.floor_y), (1.5, 3.6))
        self.assertEqual(self.session.query(Observation).count(), 2)
        self.assertEqual(workflow.api.dashboard_stats(self.session)["present_boxes"], 0)

    def test_late_and_new_floor_events_cannot_undo_collection(self):
        observe(self.payload(), self.session)
        observe(self.payload("collection", 2), self.session)
        for seconds in (-2, 4):
            result = observe(self.payload(seconds=seconds, floor_x=9), self.session)
            self.assertFalse(result["applied"])
        box = self.session.get(workflow.Box, "B1")
        self.assertEqual(box.status, "collected")
        self.assertEqual(box.floor_x, 1.5)

    def test_out_of_order_location_does_not_move_box_back(self):
        observe(self.payload(seconds=2, floor_x=5), self.session)
        result = observe(self.payload(), self.session)
        self.assertEqual(result["reason"], "out_of_order")
        self.assertEqual(self.session.get(workflow.Box, "B1").floor_x, 5)

    def test_retry_is_idempotent_and_changed_payload_is_rejected(self):
        event = self.payload()
        observe(event, self.session)
        self.assertTrue(observe(event, self.session)["duplicate"])
        self.assertEqual(self.session.query(Observation).count(), 1)
        with self.assertRaises(HTTPException) as raised:
            observe(event.model_copy(update={"floor_x": 8}), self.session)
        self.assertEqual(raised.exception.status_code, 409)

    def test_collection_without_prior_floor_sighting(self):
        observe(self.payload("collection"), self.session)
        box = self.session.get(workflow.Box, "B1")
        self.assertEqual(box.status, "collected")
        self.assertIsNone(box.floor_x)
        self.assertIsNone(box.placed_at)

    def test_missing_physical_calibration_and_invalid_time_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.payload(floor_x=None)
        with self.assertRaises(ValidationError):
            self.payload(observed_at=datetime.now())
        with self.assertRaises(ValidationError):
            self.payload(floor_x=float("nan"))

    def test_legacy_writes_cannot_corrupt_calibrated_location(self):
        observe(self.payload(), self.session)
        with self.assertRaises(HTTPException):
            workflow.api.scan(workflow.api.ScanIn(box_id="B1", station_id="OTHER"), self.session)
        with self.assertRaises(HTTPException):
            workflow.api.update_box("B1", workflow.api.BoxUpdate(status="collected"), self.session)
        self.assertEqual(self.session.get(workflow.Box, "B1").status, "placed")

    def test_concurrent_floor_write_cannot_overwrite_collection(self):
        observe(self.payload(), self.session)
        with workflow.SessionLocal() as stale:
            stale_box = stale.get(workflow.Box, "B1")
            observe(self.payload("collection", 2), self.session)
            # The stale session initially sees placed. Version conflict forces
            # observe() to retry and respect the now-collected state.
            self.assertEqual(stale_box.status, "placed")
            result = observe(self.payload(seconds=4, floor_x=9), stale)
            self.assertFalse(result["applied"])
            self.assertEqual(stale.get(workflow.Box, "B1").status, "collected")


if __name__ == "__main__":
    unittest.main()
