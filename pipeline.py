import argparse
import logging
import time

import requests

from capture import frame_stream
from config import cfg
from decoder import decode_region
from detector import BoxDetector, crop

log = logging.getLogger(__name__)
 

def _post_scan(box_id, station_id, supplier, part, bbox) -> None:
    payload = {
        "box_id": box_id,
        "station_id": station_id,
        "supplier_name": supplier,
        "part_type": part,
        "bbox": bbox,
    }
    try:
        r = requests.post(f"{cfg.api_base_url}/scan", json=payload, timeout=5)
        r.raise_for_status()
    except requests.RequestException:
        log.exception("scan POST failed for %s", box_id)


def process_frame(frame, station_id: str, detector: BoxDetector) -> int:
    """Detect → decode → POST. Returns number of decoded boxes."""
    detections = detector.detect(frame)
    decoded_count = 0
    # If YOLO returns nothing (e.g. COCO placeholder), still try to decode the
    # full frame — barcode-only workflows should work without a good detector.
    if not detections:
        for decoded in decode_region(frame):
            _post_scan(decoded.box_id, station_id, decoded.supplier, decoded.part, None)
            decoded_count += 1
        return decoded_count

    for det in detections:
        region = crop(frame, det)
        for decoded in decode_region(region):
            _post_scan(
                decoded.box_id, station_id, decoded.supplier, decoded.part,
                (det.x, det.y, det.w, det.h),
            )
            decoded_count += 1
    return decoded_count


def run_station(station_id: str, source) -> None:
    detector = BoxDetector()
    log.info("station %s: streaming from %r every %.1fs", station_id, source, cfg.scan_interval_s)
    for frame in frame_stream(source, cfg.scan_interval_s):
        started = time.monotonic()
        n = process_frame(frame, station_id, detector)
        log.info("station %s: %d boxes decoded in %.2fs", station_id, n, time.monotonic() - started)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--station", help="station id from config.cameras", default=None)
    args = parser.parse_args()

    if args.station:
        source = cfg.cameras[args.station]
        run_station(args.station, source)
        return

    # Single-station default: pick the first configured camera.
    station_id, source = next(iter(cfg.cameras.items()))
    run_station(station_id, source)


if __name__ == "__main__":
    main()
