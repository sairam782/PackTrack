import logging
from dataclasses import dataclass

import numpy as np

from config import cfg

log = logging.getLogger(__name__)


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
        self.weights = weights or cfg.yolo_weights
        self.class_ids = class_ids or cfg.box_class_ids
        self.confidence = confidence or cfg.yolo_confidence
        self._model = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO  # local import: heavy dep
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
