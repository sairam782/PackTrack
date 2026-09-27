import logging
import time
from contextlib import contextmanager

import cv2

log = logging.getLogger(__name__)


class FrameSource:
    def __init__(self, source):
        self.source = source
        self.cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            raise RuntimeError(f"could not open camera source: {self.source!r}")
        self.cap = cap

    def read(self):
        if self.cap is None:
            raise RuntimeError("source not opened")
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame

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


def frame_stream(source, interval_s: float):
    """Yield frames from `source` roughly every `interval_s` seconds."""
    with open_source(source) as src:
        while True:
            start = time.monotonic()
            frame = src.read()
            if frame is None:
                log.warning("empty frame from %r; retrying", source)
            else:
                yield frame
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, interval_s - elapsed))
