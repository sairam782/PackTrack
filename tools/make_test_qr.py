"""Generate test QR assets for the decoder and pipeline.

Single QR:
    python tools/make_test_qr.py --box-id B001 --supplier SupplierA --part "M6 Bolt"

A synthetic multi-box "factory frame" (what the pipeline expects to see):
    python tools/make_test_qr.py --scene --out testdata/scene.png
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import qrcode

DEFAULT_BOXES = [
    {"box_id": "B001", "supplier": "SupplierA", "part": "M6 Bolt"},
    {"box_id": "B002", "supplier": "SupplierB", "part": "M8 Washer"},
    {"box_id": "B003", "supplier": "SupplierA", "part": "Hex Nut"},
]


def qr_array(payload: dict, px: int = 220) -> np.ndarray:
    img = qrcode.make(json.dumps(payload)).convert("L")
    arr = np.array(img, dtype=np.uint8)
    return cv2.resize(arr, (px, px), interpolation=cv2.INTER_NEAREST)


def build_scene(boxes, width=1280, height=720) -> np.ndarray:
    # Mid-gray "factory floor" so the QR quiet zone still has contrast.
    scene = np.full((height, width, 3), 110, dtype=np.uint8)
    pad, qr_px = 40, 220
    for i, spec in enumerate(boxes):
        qr = qr_array(spec, qr_px)
        # Cardboard-ish panel with a white label the QR sits on.
        x = pad + i * (qr_px + 2 * pad)
        y = height // 2 - qr_px // 2
        cv2.rectangle(scene, (x - 20, y - 20), (x + qr_px + 20, y + qr_px + 20),
                      (138, 154, 178), -1)
        scene[y:y + qr_px, x:x + qr_px] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
        cv2.putText(scene, spec["box_id"], (x, y + qr_px + 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return scene


def build_video(path: Path, boxes, seconds=9, fps=10, width=1280, height=720) -> int:
    """Render a fixed-camera clip where boxes accumulate at the station over time."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"could not open video writer for {path}")

    total = seconds * fps
    try:
        for i in range(total):
            # Reveal one more box every `seconds/len(boxes)` seconds.
            visible = min(len(boxes), 1 + (i * len(boxes)) // total)
            writer.write(build_scene(boxes[:visible], width, height))
    finally:
        writer.release()
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", action="store_true", help="render a multi-box frame")
    parser.add_argument("--video", action="store_true", help="render a multi-box clip")
    parser.add_argument("--box-id", default="B001")
    parser.add_argument("--supplier", default="SupplierA")
    parser.add_argument("--part", default="M6 Bolt")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.video:
        out = Path(args.out or "testdata/station.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        frames = build_video(out, DEFAULT_BOXES)
        print(f"wrote {out}: {frames} frames, boxes appearing over time "
              f"({', '.join(b['box_id'] for b in DEFAULT_BOXES)})")
        return

    if args.scene:
        out = Path(args.out or "testdata/scene.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), build_scene(DEFAULT_BOXES))
        print(f"wrote {out} with {len(DEFAULT_BOXES)} boxes: "
              f"{', '.join(b['box_id'] for b in DEFAULT_BOXES)}")
        return

    out = Path(args.out or "testdata/test_qr.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"box_id": args.box_id, "supplier": args.supplier, "part": args.part}
    cv2.imwrite(str(out), cv2.cvtColor(qr_array(payload), cv2.COLOR_GRAY2BGR))
    print(f"wrote {out} with payload {json.dumps(payload)}")


if __name__ == "__main__":
    main()
