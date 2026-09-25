import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from capture import frame_stream
from config import cfg
from decoder import decode_region
from detector import BoxDetector, DetectorUnavailable, crop

log = logging.getLogger(__name__)


def _post_scan(box_id, station_id, supplier, part, bbox) -> bool:
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
        return True
    except requests.RequestException:
        log.exception("scan POST failed for %s", box_id)
        return False


def process_frame(frame, station_id: str, detector: "BoxDetector | None") -> int:
    """Detect -> crop -> decode -> POST. Returns number of boxes logged."""
    detections = []
    if detector is not None:
        try:
            detections = detector.detect(frame)
        except DetectorUnavailable as e:
            log.warning("%s falling back to whole-frame decode", e)

    # With no detector (or a placeholder COCO model that finds nothing), decode
    # the whole frame so barcode-only workflows still work.
    if not detections:
        n = 0
        for d in decode_region(frame):
            if _post_scan(d.box_id, station_id, d.supplier, d.part, None):
                n += 1
        return n

    n = 0
    for det in detections:
        for d in decode_region(crop(frame, det)):
            if _post_scan(
                d.box_id, station_id, d.supplier, d.part,
                (det.x, det.y, det.w, det.h),
            ):
                n += 1
    return n


def run_station(station_id: str, source, detect: bool = True, once: bool = False) -> int:
    detector = BoxDetector() if detect else None
    if detector is None:
        log.info("YOLO disabled; decoding full frames")
    log.info("station %s: reading %r every %.1fs", station_id, source, cfg.scan_interval_s)

    total = 0
    for frame in frame_stream(source, cfg.scan_interval_s, once=once):
        started = time.monotonic()
        n = process_frame(frame, station_id, detector)
        total += n
        log.info("station %s: %d boxes logged in %.2fs", station_id, n, time.monotonic() - started)
    return total


def run_stations(stations: dict, detect: bool = True, once: bool = False) -> int:
    """Run every configured station concurrently in one process.

    Capture is I/O bound (waiting on cameras, then on the API), so threads are
    enough -- no need for the queues or extra services the prototype rules out.
    """
    if len(stations) == 1:
        (station_id, source), = stations.items()
        return run_station(station_id, source, detect=detect, once=once)

    total = 0
    with ThreadPoolExecutor(max_workers=len(stations)) as pool:
        futures = {
            pool.submit(run_station, sid, src, detect, once): sid
            for sid, src in stations.items()
        }
        for fut in as_completed(futures):
            station_id = futures[fut]
            try:
                total += fut.result()
            except Exception:
                # One bad camera must not take down the other stations.
                log.exception("station %s failed", station_id)
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="PackTrack capture pipeline")
    parser.add_argument(
        "--station", default=None,
        help="run only this station id (default: every station in config)",
    )
    parser.add_argument(
        "--source", default=None,
        help="override camera source: USB index, RTSP URL, or path to a video/image",
    )
    parser.add_argument("--no-detect", action="store_true", help="skip YOLO, decode whole frames")
    parser.add_argument("--once", action="store_true", help="process a single frame and exit")
    args = parser.parse_args()

    if args.station:
        if args.source is None and args.station not in cfg.cameras:
            parser.error(
                f"unknown station {args.station!r}; configured: "
                f"{', '.join(cfg.cameras) or '(none)'}. Pass --source to override."
            )
        source = args.source if args.source is not None else cfg.cameras[args.station]
        stations = {args.station: source}
    elif args.source is not None:
        station_id = next(iter(cfg.cameras), "STATION-01")
        stations = {station_id: args.source}
    else:
        stations = dict(cfg.cameras)

    if not stations:
        parser.error("no cameras configured; set PACKTRACK_CAMERAS or pass --source")

    log.info("starting %d station(s): %s", len(stations), ", ".join(stations))
    total = run_stations(stations, detect=not args.no_detect, once=args.once)
    log.info("done: %d boxes logged", total)


if __name__ == "__main__":
    main()
