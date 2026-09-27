"""Read a video file, decode the labels in it, and turn them into crossings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import cv2

from decoder import decode_region
from tracking import DEFAULT_COOLDOWN_S, record_scan

log = logging.getLogger(__name__)

# Decoding every frame of a 20fps clip is wasted work: a box is in view for a
# second or more, so every third frame still catches it several times over.
DEFAULT_SAMPLE_EVERY = 3


@dataclass
class VideoResult:
    frames_read: int = 0
    frames_sampled: int = 0
    frames_decoded: int = 0
    scans: int = 0
    events: int = 0
    suppressed: int = 0
    boxes: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "frames_read": self.frames_read,
            "frames_sampled": self.frames_sampled,
            "frames_decoded": self.frames_decoded,
            "scans": self.scans,
            "events": self.events,
            "suppressed": self.suppressed,
            "boxes": self.boxes,
            "duration_s": round(self.duration_s, 2),
        }


def probe(path: str) -> dict:
    """Frame count, fps and duration, without decoding anything."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"could not open video: {path}")
    try:
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return {
            "frames": frames,
            "fps": fps,
            "duration_s": (frames / fps) if fps > 0 else 0.0,
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
        }
    finally:
        cap.release()


def process_video(
    path: str,
    station_id: str,
    session,
    *,
    sample_every: int = DEFAULT_SAMPLE_EVERY,
    base_ts: datetime | None = None,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    source: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> VideoResult:
    """Decode a clip and record what it saw.

    Event times come from the position in the video, not the clock, so the
    cooldown that collapses one box's many frames into a single crossing
    measures the footage rather than how fast the machine got through it.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"could not open video: {path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 20.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    started = base_ts or datetime.utcnow()
    label = source or f"video:{path.rsplit('/', 1)[-1]}"

    result = VideoResult()
    seen: list[str] = []

    try:
        index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            result.frames_read = index + 1

            if index % max(1, sample_every) == 0:
                result.frames_sampled += 1
                decoded = decode_region(frame)
                if decoded:
                    result.frames_decoded += 1

                ts = started + timedelta(seconds=index / fps)
                for item in decoded:
                    result.scans += 1
                    outcome = record_scan(
                        session,
                        box_id=item.box_id,
                        station_id=station_id,
                        ts=ts,
                        supplier_name=item.supplier,
                        part_type=item.part,
                        source=label,
                        frame_index=index,
                        cooldown_s=cooldown_s,
                    )
                    if outcome.recorded:
                        result.events += 1
                        if item.box_id not in seen:
                            seen.append(item.box_id)
                    else:
                        result.suppressed += 1

                if on_progress is not None:
                    on_progress(index, total)
            index += 1
    finally:
        cap.release()

    result.boxes = seen
    result.duration_s = result.frames_read / fps if fps else 0.0
    return result
