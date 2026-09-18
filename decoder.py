import json
import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from pyzbar import pyzbar

log = logging.getLogger(__name__)
 

@dataclass
class DecodedBox:
    box_id: str
    supplier: Optional[str] = None
    part: Optional[str] = None
    raw: str = ""


def _parse_payload(raw: str) -> DecodedBox:
    # Prefer JSON payloads: {"box_id": "...", "supplier": "...", "part": "..."}
    try:
        data = json.loads(raw)
        return DecodedBox(
            box_id=str(data["box_id"]),
            supplier=data.get("supplier"),
            part=data.get("part"),
            raw=raw,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fall back to treating the whole payload as an opaque box id.
        return DecodedBox(box_id=raw.strip(), raw=raw)


def decode_region(image: np.ndarray) -> list[DecodedBox]:
    """Run pyzbar on a BGR image (or crop) and return every decoded payload."""
    if image is None or image.size == 0:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    results = []
    for sym in pyzbar.decode(gray):
        try:
            raw = sym.data.decode("utf-8", errors="replace")
        except Exception:
            log.exception("failed to decode symbol bytes")
            continue
        results.append(_parse_payload(raw))
    return results


def decode_file(path: str) -> list[DecodedBox]:
    image = cv2.imread(path)
    if image is None:
        raise FileNotFoundError(path)
    return decode_region(image)
