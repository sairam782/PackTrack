"""Handheld location selection, box assignments, collection and pairing protection."""
import base64
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException

import test_workflow as workflow
from observations import ObservationIn, observe
from phone_scanner import PhoneScanner, ScanError
from phone_server import authenticated

ROOT = Path(__file__).resolve().parents[1]


def image(path):
    return base64.b64encode((ROOT / path).read_bytes()).decode()


class PhoneTests(unittest.TestCase):
    def setUp(self):
        workflow.Base.metadata.drop_all(workflow.engine)
        workflow.init_db()
        self.scanner = PhoneScanner(ROOT / 'examples/phone-locations.demo.json', 'http://local')
        self.device = str(uuid4())
        self.post = patch('requests.post', side_effect=self.save).start()
        self.addCleanup(patch.stopall)

    def save(self, url, json, **kwargs):
        with workflow.SessionLocal() as session:
            result = observe(ObservationIn(**json), session)
        return Mock(json=Mock(return_value=result))

    def scan(self, path, token=None, **kwargs):
        return self.scanner.scan(str(uuid4()), kwargs.pop('device', self.device), image(path), token, **kwargs)

    def location(self, name='location-1.png'):
        return self.scan('assets/locations/' + name)['location_token']

    def test_marker_box_then_truck_updates_existing_dashboard(self):
        token = self.location()
        result = self.scan('assets/qr/TEST-001.png', token)
        self.assertEqual(result['results'][0]['status'], 'placed')
        with workflow.SessionLocal() as session:
            box = session.get(workflow.Box, 'TEST-001')
            self.assertEqual((box.floor_x, box.floor_y), (2, 3))
            self.assertEqual(box.position_source, 'location_marker')
            self.assertEqual(box.location_id, 'ZONE-A')
            self.assertEqual(workflow.api.dashboard_stats(session)['present_boxes'], 1)
        truck = self.location('location-4.png')
        result = self.scan('assets/qr/TEST-001.png', truck)
        self.assertEqual(result['results'][0]['status'], 'collected')
        with workflow.SessionLocal() as session:
            box = session.get(workflow.Box, 'TEST-001')
            self.assertEqual((box.floor_x, box.floor_y), (2, 3))
            self.assertEqual(box.collection_camera_id, 'PHONE-TRUCK-01')
            self.assertEqual(workflow.api.dashboard_stats(session)['collected_today'], 1)

    def test_location_required_and_device_bound(self):
        with self.assertRaises(ScanError):
            self.scan('assets/qr/TEST-001.png')
        token = self.location()
        with self.assertRaises(ScanError):
            self.scan('assets/qr/TEST-001.png', token, device=str(uuid4()))
        with patch('phone_scanner.time.monotonic', return_value=10**12):
            with self.assertRaises(ScanError):
                self.scan('assets/qr/TEST-001.png', token)
        self.post.assert_not_called()

    def test_moving_to_new_marker_assigns_new_coordinates(self):
        self.scan('assets/qr/TEST-002.png', self.location())
        self.scan('assets/qr/TEST-002.png', self.location('location-2.png'))
        with workflow.SessionLocal() as session:
            box = session.get(workflow.Box, 'TEST-002')
            self.assertEqual((box.floor_x, box.floor_y), (7, 3))
            self.assertEqual(box.location_id, 'ZONE-B')

    def test_retry_same_photo_does_not_duplicate_evidence(self):
        token = self.location()
        stamp = datetime.now(timezone.utc)
        request = str(uuid4())
        for _ in range(2):
            self.scanner.scan(request, self.device, image('assets/qr/TEST-001.png'), token, stamp)
        from db import Observation
        with workflow.SessionLocal() as session:
            self.assertEqual(session.query(Observation).count(), 1)

    def test_multi_box_sheet_and_ambiguous_marker_sheet(self):
        token = self.location()
        result = self.scan('assets/qr/test-labels.png', token)
        self.assertEqual(len(result['results']), 6)
        self.assertTrue(all(row['saved'] for row in result['results']))
        with self.assertRaises(ScanError):
            self.scan('assets/locations/location-markers.png')

    def test_pairing_token_required(self):
        with patch.dict('os.environ', {'PACKTRACK_PHONE_TOKEN': 'test-secret'}):
            with self.assertRaises(HTTPException) as raised:
                authenticated('incorrect')
            self.assertEqual(raised.exception.status_code, 401)
            authenticated('test-secret')

    def test_invalid_image_is_rejected(self):
        with self.assertRaises(ScanError):
            self.scanner.scan(str(uuid4()), self.device, 'not-base64')


if __name__ == '__main__':
    unittest.main()
