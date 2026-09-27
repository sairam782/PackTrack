"""Decode QR/barcode payloads out of frames.

Two backends. pyzbar is preferred when it is importable: it reads more
symbologies and is faster. It needs the native zbar library, though, which is
a separate install and a common way for a fresh machine to fail. OpenCV ships
a QR decoder with no extra system dependency, so it stands in when pyzbar is
missing and the pipeline keeps working.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the machine
    from pyzbar import pyzbar as _pyzbar

    BACKEND = "pyzbar"
except Exception:  # ImportError, or zbar shared library missing
    _pyzbar = None
    BACKEND = "opencv"

_cv_detector = cv2.QRCodeDetector()


@dataclass
class DecodedBox:
    box_id: str
    supplier: str | None = None
    part: str | None = None
    raw: str = ""


def _parse_payload(raw: str) -> DecodedBox | None:
    """Accept JSON payloads, or treat the whole string as an opaque box id."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "box_id" in data:
            return DecodedBox(
                box_id=str(data["box_id"]),
                supplier=data.get("supplier"),
                part=data.get("part"),
                raw=raw,
            )
    except (json.JSONDecodeError, TypeError):
        pass
    return DecodedBox(box_id=raw, raw=raw)


def _decode_pyzbar(gray: np.ndarray) -> list[str]:
    payloads = []
    for sym in _pyzbar.decode(gray):
        try:
            payloads.append(sym.data.decode("utf-8", errors="replace"))
        except Exception:
            log.exception("failed to decode symbol bytes")
    return payloads


def _decode_opencv(gray: np.ndarray) -> list[str]:
    """Multi-symbol decode, with the single-symbol decoder as a backstop.

    OpenCV's detectAndDecodeMulti misses certain symbols outright -- not at a
    bad angle or a small size, but always, while detectAndDecode reads the
    very same image. Trying both costs one extra call on frames that found
    nothing, and turns a label that never scans into one that does.
    """
    found: list[str] = []
    try:
        ok, texts, _points, _ = _cv_detector.detectAndDecodeMulti(gray)
        if ok and texts is not None:
            found = [t for t in texts if t]
    except cv2.error:
        found = []

    if found:
        return found

    try:
        text, _points, _ = _cv_detector.detectAndDecode(gray)
    except cv2.error:
        return []
    return [text] if text else []


def decode_region(image: np.ndarray) -> list[DecodedBox]:
    """Return every payload decoded from a BGR image or crop."""
    if image is None or getattr(image, "size", 0) == 0:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    payloads = _decode_pyzbar(gray) if _pyzbar is not None else _decode_opencv(gray)

    seen: set[str] = set()
    out: list[DecodedBox] = []
    for raw in payloads:
        decoded = _parse_payload(raw)
        # One physical label can be reported twice in a single frame.
        if decoded is None or decoded.box_id in seen:
            continue
        seen.add(decoded.box_id)
        out.append(decoded)
    return out


def decode_file(path: str) -> list[DecodedBox]:
    image = cv2.imread(path)
    if image is None:
        raise FileNotFoundError(path)
    return decode_region(image)
