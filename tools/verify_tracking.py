"""Camera-free end-to-end camera tracking verification using an isolated API/database."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import cv2
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.make_test_qr import build_scene, DEFAULT_BOXES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="packtrack-verification-") as folder:
        folder = Path(folder)
        base = f"http://127.0.0.1:{args.port}"
        env = {**os.environ, "PACKTRACK_DB_URL": f"sqlite:///{folder}/demo.db", "PACKTRACK_API_BASE_URL": base}
        api = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1",
                                "--port", str(args.port), "--log-level", "warning"], cwd=ROOT, env=env)
        try:
            for _ in range(60):
                if api.poll() is not None:
                    raise RuntimeError("demo API exited; choose a free --port")
                try:
                    requests.get(f"{base}/dashboard", timeout=1).raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(.2)
            else:
                raise RuntimeError("API startup timed out")
            video = folder / "boxes.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (1280, 720))
            if not writer.isOpened():
                raise RuntimeError("video encoder unavailable")
            try:
                for _ in range(6):
                    writer.write(build_scene(DEFAULT_BOXES))
            finally:
                writer.release()

            def run(role):
                camera = {"camera_id": "FLOOR-A" if role == "placement" else "TRUCK-01",
                          "source": str(video), "role": role, "interval_s": .1, "confirmations": 2}
                if role == "placement":
                    camera["calibration"] = str(ROOT / "examples/floor-calibration.synthetic.json")
                else:
                    camera["roi"] = [0, 200, 300, 350]  # Only B001 is inside the simulated truck.
                config = folder / f"{role}.json"
                config.write_text(json.dumps({"cameras": [camera]}))
                subprocess.run([sys.executable, "track.py", "--config", str(config),
                                "--outbox", str(folder / "outbox.db")], cwd=ROOT, env=env,
                               check=True, timeout=30)

            def boxes():
                response = requests.get(f"{base}/boxes", timeout=5)
                response.raise_for_status()
                return {b["box_id"]: b for b in response.json()}

            run("placement")
            placed = boxes()
            assert len(placed) == 3 and all(b["status"] == "placed" for b in placed.values())
            assert abs(placed["B001"]["floor_x"] - 1.5) < .03
            assert abs(placed["B001"]["floor_y"] - 3.6) < .03
            print("PASS: floor video placed 3 boxes at calibrated x,y metres", flush=True)
            run("collection")
            collected = boxes()
            assert collected["B001"]["status"] == "collected"
            assert collected["B001"]["collection_camera_id"] == "TRUCK-01"
            assert collected["B001"]["floor_x"] == placed["B001"]["floor_x"]
            assert all(collected[b]["status"] == "placed" for b in ("B002", "B003"))
            print("PASS: truck camera collected B001; B002/B003 outside truck ROI stayed placed", flush=True)
            run("placement")
            assert boxes()["B001"]["status"] == "collected"
            response = requests.get(f"{base}/boxes/B001/observations", timeout=5)
            response.raise_for_status()
            history = response.json()
            assert any(e["role"] == "collection" and e["applied"] for e in history)
            assert any(e["reason"] == "already_collected" for e in history)
            print("PASS: later floor observations did not undo collection; evidence history retained", flush=True)
            print(json.dumps([{k: b[k] for k in ("box_id", "status", "floor_x", "floor_y", "coordinate_frame", "collection_camera_id")}
                              for b in boxes().values()], indent=2))
            print("TRACKING VERIFICATION PASSED — synthetic calibration only; no real cameras or existing data used.")
        finally:
            api.terminate()
            try:
                api.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait()


if __name__ == "__main__":
    main()
