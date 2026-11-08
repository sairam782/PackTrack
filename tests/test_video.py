import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
import qrcode
from streamlit.testing.v1 import AppTest

from video_analysis import VideoError, export_report, parse_review, review_batch, review_video, sample_video

ROOT = Path(__file__).resolve().parents[1]


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'qr.avi'
        image = np.array(qrcode.make('VIDEO-BOX-001').convert('RGB'))
        canvas = np.full((480, 640, 3), 255, dtype=np.uint8)
        h, w = image.shape[:2]
        canvas[40:40+h, 40:40+w] = image
        writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*'MJPG'), 10, (640, 480))
        self.assertTrue(writer.isOpened())
        for _ in range(20):
            writer.write(canvas)
        writer.release()

    def test_real_video_decoder_records_pixels_without_floor_coordinates(self):
        result = sample_video(self.path, 4)
        self.assertEqual(result['metadata']['duration_seconds'], 2)
        self.assertEqual(len(result['frames']), 4)
        self.assertEqual({b['box_id'] for b in result['barcodes']}, {'VIDEO-BOX-001'})
        self.assertTrue(all(0 <= b['time_seconds'] < 2 for b in result['barcodes']))
        self.assertTrue(all(b['pixel_x'] > 40 for b in result['barcodes']))
        report = export_report(result)
        self.assertNotIn('jpeg', report['frames'][0])
        self.assertFalse(report['inventory_updated'])
        self.assertNotIn('floor_x', json.dumps(report))

    def test_corrupt_video_has_actionable_error(self):
        self.path.write_bytes(b'not a video')
        with self.assertRaisesRegex(VideoError, 'could not be decoded'):
            sample_video(self.path)

    def test_model_output_must_match_frames(self):
        review = {'summary': 'Stack of bins', 'frames': [{'frame_id': 0, 'visible_containers': 3,
                  'activity': 'Stationary bins', 'labels': [{'text': 'ABC', 'confidence': 'medium'}]}]}
        self.assertEqual(parse_review(json.dumps(review), [0])['frames'][0]['visible_containers'], 3)
        with self.assertRaisesRegex(VideoError, 'mismatched'):
            parse_review(json.dumps(review), [1])
        review['frames'][0]['visible_containers'] = -1
        with self.assertRaises(VideoError):
            parse_review(json.dumps(review), [0])

    def test_failed_aws_request_preserves_local_evidence(self):
        result = sample_video(self.path, 4)
        with patch('video_analysis.requests.post', return_value=Mock(status_code=403)) as post:
            result = review_video(result, 'fake-secret')
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue(result['barcodes'])
        self.assertNotIn('fake-secret', json.dumps(export_report(result)))
        self.assertEqual(len(post.call_args.kwargs['json']['messages'][0]['content']), 9)

    def test_later_batch_maps_local_model_ids_to_video_frame_ids(self):
        frames = sample_video(self.path, 4)['frames']
        for frame in frames:
            frame['frame_id'] += 4
        reply = {'summary': 'Containers', 'frames': [
            {'frame_id': i, 'activity': 'Stacked containers', 'visible_containers': 3} for i in range(4)]}
        response = Mock(status_code=200)
        response.json.return_value = {'output': {'message': {'content': [{'text': json.dumps(reply)}]}}}
        with patch('video_analysis.requests.post', return_value=response):
            result = review_batch(frames, 'fake-secret')
        self.assertEqual([r['frame_id'] for r in result['frames']], [4, 5, 6, 7])

    def test_ui_can_analyze_video_without_key(self):
        with patch.dict(os.environ, {'PACKTRACK_SAMPLE_VIDEO': str(self.path), 'AWS_BEARER_TOKEN_BEDROCK': ''}):
            app = AppTest.from_file(str(ROOT/'video_app.py'), default_timeout=20).run()
            self.assertFalse(app.exception)
            app.radio[0].set_value('Local barcode scan').run()
            next(b for b in app.button if b.label == 'Analyze video').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[2].value, '1')
            self.assertEqual(app.metric[3].value, '0')
            app.radio[0].set_value('Nova + barcode scan').run()
            next(b for b in app.button if b.label == 'Analyze video').click().run()
            self.assertTrue(any('Enter your Bedrock key' in e.value for e in app.error))


if __name__ == '__main__':
    unittest.main()
