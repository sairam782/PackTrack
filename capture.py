import logging
import time
from contextlib import contextmanager
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


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


class FrameSource:
    def __init__(self, source):
        self.source = resolve_source(source)
        self.cap: "cv2.VideoCapture | None" = None

    def open(self) -> None:
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
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


def frame_stream(source, interval_s: float, once: bool = False, loop: bool = False):
    """Yield frames from `source` roughly every `interval_s` seconds.

    A still image yields exactly one frame. A video yields frames until it is
    exhausted (or forever, with loop=True). A camera yields until interrupted.
    `once` stops after the first frame regardless of source type.
    """
    if is_image_path(source):
        frame = cv2.imread(str(source))
        if frame is None:
            raise FileNotFoundError(f"could not read image: {source}")
        yield frame
        return

    while True:
        exhausted = False
        with open_source(source) as src:
            while True:
                started = time.monotonic()
                frame = src.read()
                if frame is None:
                    # Cameras hiccup; files simply end.
                    if isinstance(src.source, int):
                        log.warning("empty frame from %r; retrying", source)
                    else:
                        exhausted = True
                        break
                else:
                    yield frame
                    if once:
                        return
                elapsed = time.monotonic() - started
                time.sleep(max(0.0, interval_s - elapsed))
        if exhausted and not loop:
            return
