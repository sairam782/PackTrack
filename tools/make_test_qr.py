"""Generate a printable QR label for a box.

    python tools/make_test_qr.py --box-id BOX-4417 --supplier "Acme Forge"

Uses segno, which the project already depends on, so there is nothing extra
to install.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import segno

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--box-id", default="BOX-0001")
    parser.add_argument("--supplier", default=None)
    parser.add_argument("--part", default=None)
    parser.add_argument("--out", default="box_label.png")
    parser.add_argument("--scale", type=int, default=10)
    parser.add_argument(
        "--plain",
        action="store_true",
        help="encode only the box id, instead of a JSON payload",
    )
    args = parser.parse_args()

    if args.plain:
        payload = args.box_id
    else:
        data = {"box_id": args.box_id}
        if args.supplier:
            data["supplier"] = args.supplier
        if args.part:
            data["part"] = args.part
        payload = json.dumps(data)

    # make_qr rather than make. segno picks a Micro QR for short payloads, and
    # OpenCV's detector cannot read Micro QR, so a label holding just a short
    # box id would print fine and then never scan. Standard QR always.
    qr = segno.make_qr(payload, error="m")
    out = Path(args.out)
    qr.save(out, scale=args.scale, border=4)

    # Verify the artifact, not the intent.
    import cv2  # noqa: PLC0415

    import decoder  # noqa: PLC0415

    image = cv2.imread(str(out))
    decoded = decoder.decode_region(image)
    if args.box_id not in {d.box_id for d in decoded}:
        print(
            f"WARNING: {out} does not decode with the {decoder.BACKEND} backend",
            file=sys.stderr,
        )
        return 1

    print(f"wrote {out}  (QR version {qr.version}, payload: {payload})")
    print(f"verified with the {decoder.BACKEND} backend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
