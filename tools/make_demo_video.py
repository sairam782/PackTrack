"""Render demo footage: QR-labelled boxes crossing a station.

Real dock footage is not available at pitch time, and a demo that depends on
finding some is a demo that can fail on stage. This renders clips with known
box ids, so the whole pipeline -- decode, direction, dwell -- can be shown end
to end and checked against what it should have produced.

    python tools/make_demo_video.py

Writes demo/inbound.mp4 and demo/outbound.mp4 plus a manifest of the ids used.
"""

from __future__ import annotations

import io
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import segno

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "demo"

# Running this as `python tools/make_demo_video.py` puts tools/ on sys.path,
# not the repo root, so the project's own modules need help being found.
sys.path.insert(0, str(ROOT))

W, H = 1280, 720
FPS = 20
SECONDS_PER_BOX = 2.6

# (box_id, supplier, part)
FLEET = [
    ("BOX-4417", "Acme Forge", "bracket"),
    ("BOX-4418", "Acme Forge", "bracket"),
    ("BOX-5120", "Nordwerk", "bearing"),
    ("BOX-5121", "Nordwerk", "bearing"),
    ("BOX-6003", "Tamaki Plastics", "housing"),
    ("BOX-6004", "Tamaki Plastics", "housing"),
]
# Which of them leave again, in the outbound clip.
DEPARTING = ["BOX-4417", "BOX-5120", "BOX-5121", "BOX-6003"]


@dataclass
class Label:
    box_id: str
    supplier: str
    part: str
    qr: np.ndarray


# The spec's quiet zone is 4 modules, but OpenCV's detector has per-symbol
# blind spots and misses one of these labels at exactly that border while
# reading it fine at 3. pyzbar reads all of them at any border. The guard in
# main() is what actually keeps this honest.
QR_BORDER = 3


def make_qr(box_id: str, supplier: str, part: str, target_px: int = 340) -> np.ndarray:
    """Render a QR at a whole number of pixels per module.

    Resizing a finished QR to an arbitrary pixel size is what breaks it: at a
    non-integer scale, nearest-neighbour sampling drops or doubles module rows
    and the symbol stops decoding. One of six codes failed exactly this way,
    which is worse than all of them failing because it looks like it works.
    So the scale is chosen to land near the target size and the image is used
    at whatever exact size that gives, never resampled.
    """
    payload = json.dumps({"box_id": box_id, "supplier": supplier, "part": part})
    # Medium error correction, not high. High pushes this payload to a version
    # 8 symbol -- 55 modules across the same label, so six pixels per module,
    # which mp4 compression was destroying for one of the six boxes. Medium
    # gives a version 5 symbol at eight pixels per module, and all six survive
    # the encoder. Correction level buys nothing if the modules are too small
    # to recover in the first place.
    # make_qr, not make: segno picks a Micro QR for short payloads, and
    # OpenCV's detector cannot read Micro QR at all, so a short box id would
    # produce a label that silently never scans. This payload is long enough
    # to get a standard symbol either way, but the guarantee belongs here.
    qr = segno.make_qr(payload, error="m")

    modules = qr.symbol_size(scale=1, border=QR_BORDER)[0]
    scale = max(1, round(target_px / modules))

    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=QR_BORDER)
    img = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    if img is None:  # pragma: no cover - segno always produces a readable png
        raise RuntimeError(f"could not render QR for {box_id}")
    return img


def background() -> np.ndarray:
    """A dim warehouse with a lit conveyor band across the middle."""
    bg = np.zeros((H, W, 3), np.uint8)
    for y in range(H):
        shade = 26 + int(18 * math.sin(math.pi * y / H))
        bg[y, :] = (shade + 6, shade + 3, shade)

    cv2.rectangle(bg, (0, 300), (W, 560), (48, 46, 44), -1)
    cv2.rectangle(bg, (0, 300), (W, 306), (70, 68, 64), -1)
    cv2.rectangle(bg, (0, 554), (W, 560), (70, 68, 64), -1)
    for x in range(0, W, 80):  # conveyor slats
        cv2.line(bg, (x, 306), (x, 554), (40, 38, 36), 2)
    return bg


def draw_box(frame: np.ndarray, label: Label, cx: int) -> None:
    """A cardboard box centred at cx, with its label facing the camera."""
    bw, bh = 440, 440
    x1, y1 = cx - bw // 2, 170
    x2, y2 = x1 + bw, y1 + bh
    if x2 < 0 or x1 > W:
        return

    cv2.rectangle(frame, (x1 + 10, y2 - 6), (x2 + 10, y2 + 12), (18, 18, 20), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (94, 132, 176), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y1 + 26), (108, 150, 196), -1)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (58, 84, 116), 3)
    cv2.line(frame, (cx, y1), (cx, y2), (70, 100, 138), 2)

    qr = label.qr
    qh, qw = qr.shape[:2]
    qx, qy = cx - qw // 2, y1 + 34
    vx1, vy1 = max(0, qx), max(0, qy)
    vx2, vy2 = min(W, qx + qw), min(H, qy + qh)
    if vx2 <= vx1 or vy2 <= vy1:
        return

    pad = 8
    cv2.rectangle(
        frame,
        (max(0, vx1 - pad), max(0, vy1 - pad)),
        (min(W, vx2 + pad), min(H, vy2 + pad)),
        (255, 255, 255),
        -1,
    )
    frame[vy1:vy2, vx1:vx2] = qr[vy1 - qy : vy2 - qy, vx1 - qx : vx2 - qx]

    text_y = qy + qh + 26
    if 0 < text_y < H:
        cv2.putText(frame, label.box_id, (max(4, qx - 4), text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.62, (250, 250, 250), 2, cv2.LINE_AA)


def overlay(frame: np.ndarray, station: str, role: str, t: float) -> None:
    cv2.rectangle(frame, (0, 0), (W, 62), (16, 16, 18), -1)
    cv2.putText(frame, f"{station}  ({role})", (24, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (235, 235, 240), 2, cv2.LINE_AA)
    stamp = f"t+{t:05.1f}s"
    cv2.putText(frame, stamp, (W - 170, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.68, (150, 150, 160), 2, cv2.LINE_AA)


def writer(path: Path):
    """Open a video writer, falling back if the mp4 codec is unavailable."""
    for fourcc, suffix in (("mp4v", ".mp4"), ("MJPG", ".avi")):
        target = path.with_suffix(suffix)
        vw = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*fourcc), FPS, (W, H))
        if vw.isOpened():
            return vw, target
        vw.release()
    raise RuntimeError("no usable video codec (tried mp4v and MJPG)")


def render(path: Path, labels: list[Label], station: str, role: str) -> Path:
    vw, target = writer(path)
    bg = background()
    frames_per_box = int(FPS * SECONDS_PER_BOX)
    total = frames_per_box * len(labels)

    for i in range(total):
        frame = bg.copy()
        index = i // frames_per_box
        progress = (i % frames_per_box) / frames_per_box
        # Travel right to left for outbound, so the clips read differently.
        span = W + 400
        cx = int(-200 + span * progress) if role == "inbound" else int(W + 200 - span * progress)
        draw_box(frame, labels[index], cx)
        overlay(frame, station, role, i / FPS)
        vw.write(frame)

    vw.release()
    return target


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = {b: Label(b, s, p, make_qr(b, s, p)) for b, s, p in FLEET}



    inbound = render(OUT_DIR / "inbound", list(cache.values()), "STATION-IN", "inbound")
    outbound = render(
        OUT_DIR / "outbound", [cache[b] for b in DEPARTING], "STATION-OUT", "outbound"
    )

    manifest = {
        "inbound": {"file": inbound.name, "box_ids": [b for b, _, _ in FLEET]},
        "outbound": {"file": outbound.name, "box_ids": DEPARTING},
        "fleet": [{"box_id": b, "supplier": s, "part": p} for b, s, p in FLEET],
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"wrote {inbound} ({len(FLEET)} boxes)")
    print(f"wrote {outbound} ({len(DEPARTING)} boxes)")
    print(f"wrote {OUT_DIR / 'manifest.json'}")

    # Verify the encoded clips, not the source images. A QR that decodes as a
    # PNG can still be destroyed by video compression, which is exactly how a
    # label went missing here: at four pixels per module it did not survive the
    # encoder. Checking the artifact is the only check that means anything.
    missing = verify(inbound, [b for b, _, _ in FLEET]) + verify(outbound, DEPARTING)
    if missing:
        raise SystemExit(
            "these boxes never decode from the rendered clips: " + ", ".join(missing)
        )
    print("verified: every box decodes from the encoded video")
    return 0


def verify(path: Path, expected: list[str]) -> list[str]:
    """Return the ids that never decode from a written clip."""
    import decoder  # noqa: PLC0415 - local import keeps this script standalone

    cap = cv2.VideoCapture(str(path))
    seen: set[str] = set()
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % 3 == 0:
            seen.update(d.box_id for d in decoder.decode_region(frame))
        index += 1
    cap.release()
    return [b for b in expected if b not in seen]


if __name__ == "__main__":
    sys.exit(main())
