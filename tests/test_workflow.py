"""Regression tests with a temporary SQLite database; no camera or server needed."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import os
import tempfile
import unittest
from unittest.mock import patch

# Set configuration before loading app modules; never use the real database.
TMP = tempfile.TemporaryDirectory(prefix="packtrack-test-")
os.environ["PACKTRACK_DB_URL"] = f"sqlite:///{TMP.name}/test.db"

import api
import capture
import pipeline
from config import cfg
from db import Base, Box, SessionLocal, engine, init_db
from detector import Detection
from tools.make_test_qr import build_scene, DEFAULT_BOXES
from sqlalchemy import create_engine, inspect, text


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(engine)
        init_db()
        self.session = SessionLocal()

    def tearDown(self):
        self.session.close()

    def scan(self, box_id="B1", station="LINE-A", supplier="Acme", bbox=None):
        return api.scan(api.ScanIn(box_id=box_id, station_id=station,
                                  supplier_name=supplier, bbox=bbox), self.session)

    def status(self, box_id, status):
        return api.update_box(box_id, api.BoxUpdate(status=status), self.session)

    def test_full_pickup_and_reuse_lifecycle(self):
        first = self.scan()
        self.assertEqual(first.status, "active")
        self.assertEqual(api.dashboard_stats(self.session)["ready_boxes"], 0)
        empty = self.status("B1", "empty")
        self.assertEqual(empty.last_seen, first.last_seen)
        self.assertIsNotNone(empty.status_updated_at)
        queue = api.dashboard_stats(self.session)["pickup_queue"]
        self.assertEqual(queue, [{"supplier": "Acme", "station": "LINE-A", "empty_boxes": 1, "box_ids": ["B1"]}])
        self.assertEqual(self.scan().status, "empty")
        self.status("B1", "collected")
        self.assertEqual(self.scan().status, "collected")
        stats = api.dashboard_stats(self.session)
        self.assertEqual(stats["present_boxes"], 0)
        self.assertEqual(stats["pickup_queue"], [])
        self.assertEqual(stats["by_station"], {})
        self.status("B1", "active")
        self.assertEqual(self.scan(station="LINE-B").station_id, "LINE-B")
        self.assertEqual(api.dashboard_stats(self.session)["present_boxes"], 1)

    def test_stale_sighting_excluded_until_camera_rescans(self):
        self.scan()
        old = datetime.utcnow() - timedelta(seconds=cfg.stale_after_s + 5)
        self.session.get(Box, "B1").last_seen = old
        self.session.commit()
        empty = self.status("B1", "empty")
        self.assertEqual(empty.last_seen, old)
        self.assertTrue(empty.is_stale)
        stats = api.dashboard_stats(self.session)
        self.assertEqual(stats["stale_boxes"], 1)
        self.assertEqual(stats["ready_boxes"], 0)
        self.assertEqual(stats["by_station"], {})
        self.scan()
        self.assertEqual(api.dashboard_stats(self.session)["ready_boxes"], 1)

    def test_supplier_station_grouping_and_unknown_supplier(self):
        for box_id, station, supplier in [("B1", "A", "Acme"), ("B2", "B", "Acme"), ("B3", "B", None)]:
            self.scan(box_id, station, supplier)
            self.status(box_id, "empty")
        queue = api.dashboard_stats(self.session)["pickup_queue"]
        self.assertEqual(len(queue), 3)
        self.assertTrue(any(g["supplier"] is None and g["box_ids"] == ["B3"] for g in queue))

    def test_new_sighting_clears_old_coordinates(self):
        self.scan(bbox=(1, 2, 3, 4))
        self.assertIsNone(self.scan(station="LINE-B").bbox)

    def test_existing_database_gets_additive_upgrade(self):
        old_engine = create_engine(f"sqlite:///{TMP.name}/legacy.db")
        with old_engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS boxes"))
            conn.execute(text("CREATE TABLE boxes (box_id VARCHAR PRIMARY KEY, status VARCHAR)"))
            conn.execute(text("INSERT INTO boxes VALUES ('existing', 'empty')"))
        with patch("db.engine", old_engine):
            init_db()
            init_db()
        self.assertIn("status_updated_at", {c["name"] for c in inspect(old_engine).get_columns("boxes")})
        with old_engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT status FROM boxes WHERE box_id='existing'")).scalar(), "empty")
        old_engine.dispose()


class CaptureAndDecodeTests(unittest.TestCase):
    def test_partial_overlapping_detections_do_not_hide_or_duplicate_labels(self):
        class Detector:
            def detect(self, frame):
                return [Detection(20, 230, 260, 260, .99, 73)] * 2
        with patch.object(pipeline, "_post_scan", return_value=True) as post:
            count = pipeline.process_frame(build_scene(DEFAULT_BOXES), "TEST", Detector())
        self.assertEqual(count, 3)
        self.assertEqual({c.args[0] for c in post.call_args_list}, {"B001", "B002", "B003"})
        self.assertEqual(post.call_count, 3)
        self.assertIsNotNone(next(c.args[4] for c in post.call_args_list if c.args[0] == "B001"))

    def test_live_camera_retries_failed_open_then_recovers(self):
        attempts = []
        sentinel = object()
        @contextmanager
        def source(_):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("offline")
            yield object()
        with patch.object(capture, "open_source", source), \
             patch.object(capture, "_stream_live", return_value=iter([sentinel])), \
             patch.object(capture.time, "sleep") as sleep, \
             patch.object(capture, "log"):
            stream = capture.frame_stream("rtsp://test/camera", 5)
            self.assertIs(next(stream), sentinel)
            stream.close()
            self.assertEqual(len(attempts), 3)
            self.assertEqual(sleep.call_count, 2)

    def test_once_and_file_errors_fail_promptly(self):
        with patch.object(capture, "open_source", side_effect=RuntimeError("offline")):
            for source, once in [("rtsp://test/camera", True), ("missing.mp4", False)]:
                with self.assertRaises(RuntimeError):
                    next(capture.frame_stream(source, 5, once=once))


if __name__ == "__main__":
    unittest.main()
