"""Map a barcode centre on a calibrated horizontal plane to factory x,y metres."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


@dataclass
class Calibration:
    image_size: tuple[int, int]
    image_points: np.ndarray
    floor_points: np.ndarray
    coordinate_frame: str
    plane_height_m: float
    calibration_id: str
    matrix: np.ndarray

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_dict(cls, data):
        if data.get("units") != "metres":
            raise ValueError("calibration units must be metres")
        size = data["image_size"]
        if len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
            raise ValueError("image_size must contain positive integer width and height")
        pixel = np.asarray(data["image_points"], dtype=np.float32)
        floor = np.asarray(data["floor_points"], dtype=np.float32)
        for name, points in (("image_points", pixel), ("floor_points", floor)):
            if points.shape != (4, 2) or not np.isfinite(points).all():
                raise ValueError(f"{name} must have four finite x,y points")
            if not cv2.isContourConvex(points) or abs(cv2.contourArea(points)) < 1e-6:
                raise ValueError(f"{name} must form a nondegenerate convex quadrilateral in matching perimeter order")
        if (pixel < 0).any() or (pixel[:, 0] >= size[0]).any() or (pixel[:, 1] >= size[1]).any():
            raise ValueError("calibration points must be inside the reference image")
        height = float(data["plane_height_m"])
        if not np.isfinite(height) or height < 0:
            raise ValueError("plane_height_m must be a finite nonnegative height")
        frame = data["coordinate_frame"]
        if not isinstance(frame, str) or not frame.strip():
            raise ValueError("coordinate_frame is required (for example factory-floor)")
        matrix = cv2.getPerspectiveTransform(pixel, floor)
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
            raise ValueError("calibration is singular")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
        return cls(tuple(size), pixel, floor, frame, height, fingerprint, matrix)

    def project(self, x, y, frame_size):
        if tuple(frame_size) != self.image_size:
            raise ValueError(f"camera resolution {tuple(frame_size)} differs from calibration {self.image_size}; recalibrate")
        if cv2.pointPolygonTest(self.image_points, (float(x), float(y)), False) < 0:
            return None  # Do not extrapolate outside the measured area.
        value = self.matrix @ np.array([x, y, 1.0])
        if abs(value[2]) < 1e-10:
            return None
        world = value[:2] / value[2]
        return (float(world[0]), float(world[1])) if np.isfinite(world).all() else None
