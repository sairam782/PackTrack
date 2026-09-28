"""Paired phone scanner: trusted location QR -> box QR -> observation API."""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import secrets
import threading
import time
from uuid import UUID, uuid5

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
import requests

from decoder import decode_region


class ScanError(ValueError):
    pass


class PhoneScanner:
    def __init__(self, locations_path, api_base_url):
        data = json.loads(Path(locations_path).read_text())
        self.frame = data["coordinate_frame"]
        if not isinstance(self.frame, str) or not self.frame.strip():
            raise ValueError("coordinate_frame is required")
        self.example_coordinates = bool(data.get("example_coordinates", False))
        self.locations = {}
        for row in data["locations"]:
            identifier = row["location_id"]
            if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 200 or identifier in self.locations:
                raise ValueError("location IDs must be nonempty and unique")
            if row["role"] not in {"placement", "collection"}:
                raise ValueError("location role must be placement or collection")
            if row["role"] == "placement" and not all(isinstance(row.get(k), (float, int)) and math.isfinite(row[k]) for k in ("x", "y")):
                raise ValueError("placement locations require finite x,y metre coordinates")
            self.locations[identifier] = row
        if not self.locations:
            raise ValueError("at least one location is required")
        self.calibration_id = "marker-map-" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
        self.api_base_url = api_base_url.rstrip("/")
        self.contexts = {}
        self.lock = threading.Lock()

    def description(self):
        return {"coordinate_frame": self.frame, "example_coordinates": self.example_coordinates,
                "locations": list(self.locations.values()), "context_seconds": 120}

    def _decode(self, data):
        try:
            raw = base64.b64decode(data, validate=True)
            if len(raw) > 6_000_000:
                raise ScanError("Image too large. Use a smaller photo.")
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 20_000_000:
                    raise ScanError("Image too large. Use a photo below 20 megapixels.")
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2400, 2400))
                frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        except (ValueError, OSError, UnidentifiedImageError) as exc:
            raise ScanError("Could not read the photo. Use JPEG or PNG.") from exc
        return decode_region(frame)

    def scan(self, request_id, device_id, image, location_token=None, captured_at=None):
        request_id, device_id = UUID(str(request_id)), str(UUID(str(device_id)))
        captured_at = captured_at or datetime.now(timezone.utc)
        decoded = self._decode(image)
        if not decoded:
            return {"kind": "no_code", "message": "No QR found. Move closer and keep the full code and white border visible."}
        marker_ids, boxes = set(), {}
        for hit in decoded:
            try:
                data = json.loads(hit.raw)
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("type") == "packtrack_location":
                identifier = data.get("location_id")
                if identifier not in self.locations:
                    raise ScanError("This location marker is not configured for this PackTrack server.")
                marker_ids.add(identifier)
            else:
                boxes.setdefault(hit.box_id, hit)
        if marker_ids:
            if len(marker_ids) != 1:
                raise ScanError("More than one location marker is visible. Scan only the location you are using.")
            identifier = next(iter(marker_ids))
            token = secrets.token_urlsafe(24)
            with self.lock:
                now = time.monotonic()
                self.contexts = {k: v for k, v in self.contexts.items() if v[2] > now}
                self.contexts[token] = (device_id, identifier, now + 120)
            return {"kind": "location", "location_token": token, "expires_in": 120,
                    "location": self.locations[identifier], "coordinate_frame": self.frame,
                    "message": "Location selected. Now scan box labels; keep location markers out of the next photo."}
        with self.lock:
            context = self.contexts.get(location_token)
        if not context or context[0] != device_id or context[2] <= time.monotonic():
            raise ScanError("Scan the location marker first. Location selection expires after two minutes.")
        location = self.locations[context[1]]
        results = []
        for box_id, hit in boxes.items():
            if not box_id or len(box_id) > 200 or "/" in box_id:
                results.append({"box_id": box_id[:100], "saved": False, "message": "Invalid box ID"})
                continue
            x, y, w, h = hit.rect
            payload = {
                "event_id": str(uuid5(request_id, box_id)), "box_id": box_id,
                "camera_id": "PHONE-" + location["location_id"], "role": location["role"],
                "observed_at": captured_at.isoformat(), "supplier_name": hit.supplier, "part_type": hit.part,
                "pixel_x": x + w / 2, "pixel_y": y + h / 2,
                "position_source": "location_marker", "location_id": location["location_id"],
            }
            if location["role"] == "placement":
                payload.update(floor_x=location["x"], floor_y=location["y"],
                               coordinate_frame=self.frame, calibration_id=self.calibration_id)
            try:
                response = requests.post(f"{self.api_base_url}/observations", json=payload, timeout=5)
                response.raise_for_status()
                result = response.json()
            except requests.RequestException:
                results.append({"box_id": box_id, "saved": False, "message": "Tracking service unavailable or rejected this scan. Retry this photo."})
                continue
            results.append({"box_id": box_id, "saved": True, "status": result["status"],
                            "applied": result["applied"], "reason": result["reason"],
                            "location_id": location["location_id"],
                            "x": location.get("x"), "y": location.get("y")})
        return {"kind": "boxes", "results": results, "location": location,
                "message": "Dashboard updated." if results and all(r["saved"] for r in results) else "Some scans need retrying."}
