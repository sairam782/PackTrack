"""Create a measured camera calibration from four matching reference points."""
import argparse
import json
from pathlib import Path
import sys

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibration import Calibration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path, help="reference frame from the fixed camera")
    parser.add_argument("--image-points", required=True, type=json.loads, help='four pixel points, e.g. "[[10,10],[900,10],[900,700],[10,700]]"')
    parser.add_argument("--floor-points", required=True, type=json.loads, help="four matching factory x,y points in metres, in the same perimeter order")
    parser.add_argument("--plane-height-m", required=True, type=float, help="height of the horizontal reference/label plane above the floor")
    parser.add_argument("--coordinate-frame", default="factory-floor")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    frame = cv2.imread(str(args.image))
    if frame is None:
        parser.error("could not read reference frame")
    height, width = frame.shape[:2]
    data = {
        "image_size": [width, height], "image_points": args.image_points,
        "floor_points": args.floor_points, "units": "metres",
        "coordinate_frame": args.coordinate_frame, "plane_height_m": args.plane_height_m,
    }
    calibration = Calibration.from_dict(data)
    if args.out.exists():
        parser.error("output exists; choose a new calibration filename to preserve its provenance")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Saved {args.out}; calibration ID {calibration.calibration_id}")
    print(f"Valid only inside the four reference points, at {args.plane_height_m:g}m label height.")
    print("Validate against additional measured points before tracking real boxes.")


if __name__ == "__main__":
    main()
