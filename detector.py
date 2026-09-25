import logging
from dataclasses import dataclass

import numpy as np

from config import cfg

log = logging.getLogger(__name__)
 

class DetectorUnavailable(RuntimeError):
    """YOLO extras are not installed; callers may fall back to whole-frame decode."""


@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    class_id: int


class BoxDetector:
    """Thin wrapper around ultralytics YOLOv8 that filters to box-like classes."""

    def __init__(
        self,
        weights: str | None = None,
        class_ids: tuple[int, ...] | None = None,
        confidence: float | None = None,
    ):
        # Explicit None checks: an empty class_ids tuple means "keep every
        # class" and a confidence of 0.0 is legitimate, so `or` would silently
        # replace both with the configured defaults.
        self.weights = cfg.yolo_weights if weights is None else weights
        self.class_ids = cfg.box_class_ids if class_ids is None else tuple(class_ids)
        self.confidence = cfg.yolo_confidence if confidence is None else confidence
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from ultralytics import YOLO  # local import: heavy dep
            except ModuleNotFoundError as e:
                raise DetectorUnavailable(
                    "ultralytics is not installed. Either install the detection "
                    "extras (pip install -r requirements-detect.txt) or run the "
                    "pipeline with --no-detect to decode whole frames."
                ) from e
            log.info("loading YOLO weights: %s", self.weights)
            self._model = YOLO(self.weights)
        return self._model

    def detect(self, frame: np.ndarray) -> list[Detection]:
        model = self._load()
        results = model.predict(frame, conf=self.confidence, verbose=False)
        detections: list[Detection] = []
        for res in results:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                cls = int(b.cls.item())
                if self.class_ids and cls not in self.class_ids:
                    continue
                x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                detections.append(Detection(
                    x=x1, y=y1,
                    w=max(0, x2 - x1), h=max(0, y2 - y1),
                    confidence=float(b.conf.item()),
                    class_id=cls,
                ))
        return detections


def crop(frame: np.ndarray, det: Detection) -> np.ndarray:
    h, w = frame.shape[:2]
    x1 = max(0, det.x)
    y1 = max(0, det.y)
    x2 = min(w, det.x + det.w)
    y2 = min(h, det.y + det.h)
    return frame[y1:y2, x1:x2]
