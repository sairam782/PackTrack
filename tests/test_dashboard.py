"""Exercise the integrated dashboard without a browser or network."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

import test_workflow as workflow
from demo import DemoWorld

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def setUp(self):
        workflow.Base.metadata.drop_all(workflow.engine)
        workflow.init_db()
        self.world = DemoWorld()
        self.world.tick()
        self.world.tick()
        self.mode = patch.object(workflow.cfg, "demo_mode", True)
        self.mode.start()
        workflow.api.app.state.demo = self.world
        self.get = patch("requests.get", side_effect=self.fetch).start()
        self.post = patch("requests.post", side_effect=self.update).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(lambda: setattr(workflow.api.app.state, "demo", None))

    def fetch(self, url, **kwargs):
        with workflow.SessionLocal() as session:
            if url.endswith("/dashboard"):
                data = workflow.api.dashboard_stats(session)
            elif url.endswith("/activity"):
                data = workflow.api.activity(limit=30, session=session)
                for event in data:
                    event["observed_at"] = event["observed_at"].isoformat()
            elif url.endswith("/phone-connection"):
                data = {"enabled": False, "url": None}
            else:
                data = [workflow.api.BoxOut.from_row(b).model_dump(mode="json")
                        for b in session.query(workflow.Box).all()]
        return Mock(json=Mock(return_value=data))

    def update(self, url, json, **kwargs):
        if url.endswith("/demo/collect"):
            self.world.collect(json["box_id"])
        elif url.endswith("/demo/automation"):
            self.world.auto_collect = json["enabled"]
        elif url.endswith("/demo/batch"):
            self.world.add_batch()
        return Mock()

    def app(self):
        app = AppTest.from_file(str(ROOT / "dashboard.py"), default_timeout=15).run()
        self.assertEqual(len(app.exception), 0, str(app.exception))
        return app

    def test_floor_counts_collection_and_register_filter(self):
        app = self.app()
        self.assertEqual([m.value for m in app.metric], ["6", "0", "0", "6"])
        next(b for b in app.button if b.label == "Load into truck").click().run()
        self.assertEqual(len(app.exception), 0, str(app.exception))
        self.assertEqual([m.value for m in app.metric], ["5", "1", "0", "6"])
        app.selectbox(key="status").select("Collected").run()
        self.assertEqual(len(app.exception), 0, str(app.exception))
        tables = [d.value for d in app.dataframe if "Box" in d.value.columns and "Supplier" in d.value.columns]
        self.assertEqual(tables[0]["Box"].tolist(), ["TEST-001"])
        self.assertEqual(len(app.get("imgs")), 6)

    def test_unreachable_api_has_recovery_message(self):
        self.get.side_effect = requests.ConnectionError("offline")
        app = self.app()
        self.assertEqual(len(app.metric), 0)
        self.assertIn("Cannot reach", app.error[0].value)

    def test_failed_demo_update_does_not_claim_collection(self):
        self.post.side_effect = requests.ConnectionError("offline")
        app = self.app()
        next(b for b in app.button if b.label == "Load into truck").click().run()
        self.assertEqual(len(app.exception), 0, str(app.exception))
        self.assertTrue(any("could not complete" in e.value for e in app.error))
        self.assertEqual(app.metric[1].value, "0")

    def test_demo_auto_play_can_start_and_stop(self):
        app = self.app()
        next(b for b in app.button if b.label == "Auto-play pickups").click().run()
        self.assertTrue(self.world.auto_collect)
        next(b for b in app.button if b.label == "Pause pickups").click().run()
        self.assertFalse(self.world.auto_collect)
        self.assertEqual(len(app.exception), 0, str(app.exception))

    def test_supplier_totals_and_collection_history_are_consistent(self):
        self.world.collect("TEST-001")
        with workflow.SessionLocal() as session:
            stats = workflow.api.dashboard_stats(session)
            group = next(s for s in stats["supplier_summary"] if s["supplier"] == "Acme Components")
            self.assertEqual(group, {"supplier": "Acme Components", "on_floor": 1, "collected": 1, "stale": 0})
            self.assertEqual(sum(h["boxes"] for h in stats["collection_timeline"]), 1)
            self.assertEqual(stats["collected_today"], 1)
            events = workflow.api.activity(limit=30, session=session)
            self.assertTrue(any(e["event"] == "collection" and e["box_id"] == "TEST-001" for e in events))
        self.world.tick()
        with workflow.SessionLocal() as session:
            self.assertEqual(session.get(workflow.Box, "TEST-001").status, "collected")

    def test_live_mode_has_no_simulation_controls(self):
        workflow.cfg.demo_mode = False
        app = self.app()
        self.assertFalse(any(b.label == "Load into truck" for b in app.button))
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            workflow.api.demo_world()


if __name__ == "__main__":
    unittest.main()
