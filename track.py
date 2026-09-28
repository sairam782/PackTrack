"""Run camera tracking: .venv/bin/python track.py --config cameras.json"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
import threading

from camera_tracking import Camera, Outbox, Tracker
from capture import timed_frame_stream
from config import cfg

log = logging.getLogger(__name__)


def run_camera(camera, outbox, stop):
    tracker = Tracker(camera)
    log.info("starting %s (%s)", camera.camera_id, camera.role)
    for frame, captured_at in timed_frame_stream(camera.source, camera.interval_s, stop):
        if stop.is_set():
            break
        if frame is None:
            tracker.reset()
            continue
        for event in tracker.process(frame, captured_at):
            outbox.enqueue(event)
            log.info("%s %s %s floor=(%s, %s)", camera.camera_id, event["role"],
                     event["box_id"], event.get("floor_x"), event.get("floor_y"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="camera configuration JSON")
    parser.add_argument("--outbox", type=Path, default=Path("camera-outbox.db"))
    parser.add_argument("--flush-only", action="store_true", help="retry saved events, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.config and not args.flush_only:
        parser.error("--config is required unless --flush-only is used")
    cameras = Camera.load_all(args.config) if args.config else []
    outbox = Outbox(args.outbox)
    if args.flush_only:
        while outbox.flush(cfg.api_base_url):
            pass
        return 1 if outbox.count() else 0

    stop = threading.Event()
    delivery_stop = threading.Event()
    def deliver():
        while not delivery_stop.is_set():
            outbox.flush(cfg.api_base_url)
            delivery_stop.wait(1)
    delivery = threading.Thread(target=deliver, daemon=True)
    delivery.start()
    pool = ThreadPoolExecutor(max_workers=len(cameras))
    failed = False
    try:
        futures = {pool.submit(run_camera, camera, outbox, stop): camera.camera_id for camera in cameras}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                failed = True
                log.exception("camera %s stopped; fix its configuration and restart", futures[future])
    except KeyboardInterrupt:
        log.info("stopping cameras")
    finally:
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        delivery_stop.set()
        delivery.join()
        while outbox.flush(cfg.api_base_url):
            pass
    remaining = outbox.count()
    if remaining:
        log.warning("%d events saved in %s; use --flush-only to retry", remaining, args.outbox)
    return 1 if failed or remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
