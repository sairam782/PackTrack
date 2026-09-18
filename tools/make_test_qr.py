"""Generate a test QR image for the decoder.

Requires the optional `qrcode` package: pip install "qrcode[pil]"
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--box-id", default="B001")
    parser.add_argument("--supplier", default="SupplierA")
    parser.add_argument("--part", default="M6 Bolt")
    parser.add_argument("--out", default="test_qr.png")
    args = parser.parse_args()

    import qrcode  # local import so the main app doesn't depend on it
    payload = json.dumps({
        "box_id": args.box_id, "supplier": args.supplier, "part": args.part,
    })
    img = qrcode.make(payload)
    out = Path(args.out)
    img.save(out)
    print(f"wrote {out} with payload {payload}")


if __name__ == "__main__":
    main()
