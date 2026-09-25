import json
import os
from dataclasses import dataclass, field

DEFAULT_CAMERAS = {"STATION-01": 0}


def _coerce_source(value):
    """USB indices stay ints; paths and RTSP/HTTP URLs stay strings."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _load_cameras() -> dict:
    """Read the station -> source map from PACKTRACK_CAMERAS.

    Accepts JSON ({"STATION-01": 0, "STATION-02": "rtsp://..."}) or a compact
    comma-separated form (STATION-01=0,STATION-02=/clips/line2.mp4).
    """
    raw = os.getenv("PACKTRACK_CAMERAS", "").strip()
    if not raw:
        return dict(DEFAULT_CAMERAS)

    if raw.startswith("{"):
        return {k: _coerce_source(v) for k, v in json.loads(raw).items()}

    cameras = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        station, _, source = pair.partition("=")
        if not source:
            raise ValueError(
                f"malformed PACKTRACK_CAMERAS entry {pair!r}; expected STATION=source"
            )
        cameras[station.strip()] = _coerce_source(source)
    return cameras
 
 
def _load_box_classes() -> tuple:
    raw = os.getenv("PACKTRACK_BOX_CLASSES", "").strip()
    if not raw:
        return (73,)
    if raw.lower() == "all":
        return ()
    return tuple(int(part) for part in raw.split(",") if part.strip())


@dataclass
class Config:
    db_url: str = os.getenv("PACKTRACK_DB_URL", "sqlite:///./packtrack.db")

    api_host: str = os.getenv("PACKTRACK_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("PACKTRACK_API_PORT", "8000"))
    api_base_url: str = os.getenv("PACKTRACK_API_BASE_URL", "http://127.0.0.1:8000")

    # Camera sources: station_id -> OpenCV VideoCapture source.
    # Ints are USB device indices; strings are RTSP URLs or file paths.
    cameras: dict = field(default_factory=lambda: _load_cameras())

    scan_interval_s: float = float(os.getenv("PACKTRACK_SCAN_INTERVAL", "5.0"))

    yolo_weights: str = os.getenv("PACKTRACK_YOLO_WEIGHTS", "yolov8n.pt")
    # Class ids treated as "box-like". Defaults to COCO 73 (book), the nearest
    # rectangular stand-in until a custom box model is trained -- it will not
    # reliably find real packaging. Set PACKTRACK_BOX_CLASSES to a comma list,
    # or to "all" to keep every class the model reports.
    box_class_ids: tuple = field(default_factory=lambda: _load_box_classes())
    yolo_confidence: float = float(os.getenv("PACKTRACK_YOLO_CONF", "0.25"))


cfg = Config()
