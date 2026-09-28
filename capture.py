import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
import threading

import cv2

from config import cfg

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}

# Upper bound on frames discarded when catching up to a live stream, so a
# camera producing frames faster than we drain them cannot wedge the loop.
MAX_DRAIN = 120


def resolve_source(source):
    """Normalise a config/CLI source into something VideoCapture understands.

    Accepts a USB device index (int or digit string), an RTSP URL, or a path to
    a video or still image.
    """
    if isinstance(source, int):
        return source
    s = str(source)
    if s.isdigit():
        return int(s)
    return s


def is_image_path(source) -> bool:
    if isinstance(source, int):
        return False
    s = str(source)
    return not s.isdigit() and Path(s).suffix.lower() in IMAGE_SUFFIXES


def is_live_source(source) -> bool:
    """True for USB cameras and network streams, false for files on disk."""
    resolved = resolve_source(source)
    if isinstance(resolved, int):
        return True
    return str(resolved).split("://", 1)[0].lower() in {"rtsp", "rtmp", "http", "https"}


class FrameSource:
    def __init__(self, source):
        self.source = resolve_source(source)
        self.cap: "cv2.VideoCapture | None" = None

    def open(self) -> None:
        if is_live_source(self.source) and not isinstance(self.source, int):
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
            ])
        else:
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"could not open camera source: {self.source!r} "
                "(for a USB webcam on macOS, grant camera permission to your terminal "
                "in System Settings > Privacy & Security > Camera)"
            )
        self.cap = cap

    def read(self):
        if self.cap is None:
            raise RuntimeError("source not opened")
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame

    def read_latest(self):
        """Read the newest frame, discarding anything buffered behind it.

        Live streams queue frames while we are busy decoding; without draining,
        each pass falls one interval further behind real time.
        """
        if self.cap is None:
            raise RuntimeError("source not opened")
        for _ in range(MAX_DRAIN):
            if not self.cap.grab():
                break
        ok, frame = self.cap.retrieve()
        if not ok:
            return self.read()
        return frame

    def seek_frame(self, index: int) -> bool:
        if self.cap is None:
            raise RuntimeError("source not opened")
        return bool(self.cap.set(cv2.CAP_PROP_POS_FRAMES, index))

    @property
    def fps(self) -> float:
        if self.cap is None:
            return 0.0
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if fps and fps > 0 else 0.0

    @property
    def frame_count(self) -> int:
        if self.cap is None:
            return 0
        return int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


@contextmanager
def open_source(source):
    src = FrameSource(source)
    src.open()
    try:
        yield src
    finally:
        src.close()


def _stream_file(src: FrameSource, interval_s: float, once: bool):
    """Sample a video file every `interval_s` of *footage* by seeking.

    Sleeping between reads would make a short clip take minutes to walk, so
    step by frame index instead and run as fast as decoding allows.
    """
    fps = src.fps or 30.0
    step = max(1, round(interval_s * fps))
    total = src.frame_count
    index = 0
    while total == 0 or index < total:
        if index and not src.seek_frame(index):
            return
        frame = src.read()
        if frame is None:
            return
        yield frame
        if once:
            return
        index += step


def _stream_live(src: FrameSource, interval_s: float, once: bool):
    """Sample a camera or network stream, always taking the newest frame."""
    while True:
        started = time.monotonic()
        frame = src.read_latest()
        if frame is None:
            log.warning("empty frame from live source; reconnecting")
            return
        yield frame
        if once:
            return
        time.sleep(max(0.0, interval_s - (time.monotonic() - started)))


def frame_stream(source, interval_s: float, once: bool = False, loop: bool = False):
    """Yield frames from `source` every `interval_s` seconds.

    Still images yield exactly one frame. Video files are sampled by seeking,
    so `interval_s` means "every N seconds of footage" and the clip is walked
    at decode speed. Live sources (USB index, rtsp://, http://) are drained to
    the newest frame each pass so the pipeline cannot fall behind real time.
    """
    if is_image_path(source):
        frame = cv2.imread(str(source))
        if frame is None:
            raise FileNotFoundError(f"could not read image: {source}")
        yield frame
        return

    live = is_live_source(source)
    while True:
        try:
            with open_source(source) as src:
                if live:
                    yield from _stream_live(src, interval_s, once)
                else:
                    yield from _stream_file(src, interval_s, once)
        except (RuntimeError, cv2.error):
            if not live or once:
                raise
            log.exception("live camera unavailable; retrying")
        if once or (not live and not loop):
            return
        if live:
            log.warning("reconnecting in %.1fs", cfg.reconnect_delay_s)
            time.sleep(cfg.reconnect_delay_s)


def timed_frame_stream(source, interval_s, stop):
    """camera tracking capture: timestamp at read time and continuously drain live cameras.

    The producer alone owns the capture handle. A one-frame queue discards old
    frames while barcode processing runs, without grabbing 120 *new* frames
    before each observation. None signals a connection gap to reset confirmation.
    """
    if not is_live_source(source):
        for frame in frame_stream(source, interval_s):
            if stop.is_set():
                return
            yield frame, datetime.now(timezone.utc)
        return

    frames = Queue(maxsize=1)
    finished = threading.Event()

    def publish(frame):
        item = (frame, datetime.now(timezone.utc))
        try:
            frames.put_nowait(item)
        except Full:
            try:
                frames.get_nowait()
            except Empty:
                pass
            frames.put_nowait(item)

    def read_continuously():
        while not stop.is_set() and not finished.is_set():
            try:
                with open_source(source) as src:
                    while not stop.is_set() and not finished.is_set():
                        frame = src.read()
                        publish(frame)
                        if frame is None:
                            break
            except (RuntimeError, cv2.error):
                log.warning("camera open/read failed; retrying")
                publish(None)
            finished.wait(cfg.reconnect_delay_s)

    reader = threading.Thread(target=read_continuously, daemon=True)
    reader.start()
    try:
        while not stop.is_set():
            try:
                yield frames.get(timeout=1)
            except Empty:
                yield None, datetime.now(timezone.utc)
            stop.wait(interval_s)
    finally:
        finished.set()
        reader.join(timeout=1)
