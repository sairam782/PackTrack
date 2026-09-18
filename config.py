import os
from dataclasses import dataclass, field
 
 
@dataclass
class Config:
    db_url: str = os.getenv("PACKTRACK_DB_URL", "sqlite:///./packtrack.db")

    api_host: str = os.getenv("PACKTRACK_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("PACKTRACK_API_PORT", "8000"))
    api_base_url: str = os.getenv("PACKTRACK_API_BASE_URL", "http://127.0.0.1:8000")

    # Camera sources: station_id -> OpenCV VideoCapture source.
    # Ints are USB device indices; strings are RTSP URLs or file paths.
    cameras: dict = field(default_factory=lambda: {
        "STATION-01": int(os.getenv("PACKTRACK_CAM_0", "0")),
    })

    scan_interval_s: float = float(os.getenv("PACKTRACK_SCAN_INTERVAL", "5.0"))

    yolo_weights: str = os.getenv("PACKTRACK_YOLO_WEIGHTS", "yolov8n.pt")
    # COCO class ids we treat as "box-like" until a custom model is trained.
    # 73 = book, 63 = laptop are placeholders; adjust once a real model exists.
    box_class_ids: tuple = (73,)
    yolo_confidence: float = float(os.getenv("PACKTRACK_YOLO_CONF", "0.25"))


cfg = Config()
