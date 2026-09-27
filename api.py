"""PackTrack API: scans in, crossings and dashboard data out."""

from __future__ import annotations

import tempfile
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

import decoder
import video
from config import cfg
from db import (
    Box,
    BoxEvent,
    BoxState,
    BoxStatus,
    Direction,
    SessionLocal,
    Station,
    StationRole,
    Supplier,
    get_session,
    init_db,
)
from jobs import Job, registry
from tracking import get_or_create_station, record_scan

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DEMO_DIR = ROOT / "demo"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

app = FastAPI(title="PackTrack API", version="0.2.0")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class ScanIn(BaseModel):
    box_id: str
    station_id: str
    supplier_name: str | None = None
    part_type: str | None = None
    bbox: tuple[int, int, int, int] | None = Field(
        default=None, description="(x, y, w, h) in pixels"
    )


class BoxOut(BaseModel):
    box_id: str
    station_id: str
    supplier_name: str | None = None
    part_type: str | None = None
    first_seen: datetime
    last_seen: datetime
    status: str
    state: str
    checked_in_at: datetime | None = None
    checked_out_at: datetime | None = None
    dwell_seconds: float | None = None
    bbox: tuple[int, int, int, int] | None = None

    @classmethod
    def from_row(cls, box: Box) -> BoxOut:
        bbox = None
        if None not in (box.bbox_x, box.bbox_y, box.bbox_w, box.bbox_h):
            bbox = (box.bbox_x, box.bbox_y, box.bbox_w, box.bbox_h)
        return cls(
            box_id=box.box_id,
            station_id=box.station_id,
            supplier_name=box.supplier.supplier_name if box.supplier else None,
            part_type=box.part_type,
            first_seen=box.first_seen,
            last_seen=box.last_seen,
            status=box.status,
            state=box.state,
            checked_in_at=box.checked_in_at,
            checked_out_at=box.checked_out_at,
            dwell_seconds=box.dwell_seconds,
            bbox=bbox,
        )


class EventOut(BaseModel):
    event_id: int
    box_id: str
    station_id: str
    direction: str
    ts: datetime
    source: str

    @classmethod
    def from_row(cls, event: BoxEvent) -> EventOut:
        return cls(
            event_id=event.event_id,
            box_id=event.box_id,
            station_id=event.station_id,
            direction=event.direction,
            ts=event.ts,
            source=event.source,
        )


class BoxUpdate(BaseModel):
    status: BoxStatus


class StationIn(BaseModel):
    station_id: str
    station_name: str | None = None
    role: StationRole = StationRole.INBOUND


class RoleUpdate(BaseModel):
    role: StationRole


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


@app.on_event("startup")
def _startup() -> None:
    init_db()
    session = SessionLocal()
    try:
        for station_id, cam in cfg.cameras.items():
            if not session.get(Station, station_id):
                session.add(
                    Station(
                        station_id=station_id,
                        station_name=station_id,
                        camera_url=str(cam),
                        role=StationRole.INBOUND.value,
                    )
                )
        # The demo clips name their own stations, so make them exist with the
        # right roles whether or not a camera is configured.
        for station_id, role in (
            ("STATION-IN", StationRole.INBOUND),
            ("STATION-OUT", StationRole.OUTBOUND),
        ):
            if not session.get(Station, station_id):
                session.add(
                    Station(
                        station_id=station_id,
                        station_name=station_id.replace("-", " ").title(),
                        role=role.value,
                    )
                )
        session.commit()
    finally:
        session.close()


@app.get("/api/health")
def health(session: Session = Depends(get_session)) -> dict:
    return {
        "ok": True,
        "version": app.version,
        # Which decoder is live matters: OpenCV needs no system library but has
        # per-symbol blind spots, so the UI says which one is in use.
        "qr_backend": decoder.BACKEND,
        "qr_backend_note": (
            "pyzbar (zbar) — most reliable"
            if decoder.BACKEND == "pyzbar"
            else "OpenCV fallback — install libzbar for best reliability"
        ),
        "boxes": session.query(func.count(Box.box_id)).scalar() or 0,
        "events": session.query(func.count(BoxEvent.event_id)).scalar() or 0,
        "demo_clips": sorted(p.name for p in DEMO_DIR.glob("*.mp4")) if DEMO_DIR.is_dir() else [],
    }


# --------------------------------------------------------------------------
# Scans and boxes
# --------------------------------------------------------------------------


@app.post("/api/scan", response_model=BoxOut)
def scan(payload: ScanIn, session: Session = Depends(get_session)) -> BoxOut:
    """Record one sighting. The station's role decides in or out."""
    record_scan(
        session,
        box_id=payload.box_id,
        station_id=payload.station_id,
        supplier_name=payload.supplier_name,
        part_type=payload.part_type,
        bbox=payload.bbox,
        source="live",
    )
    box = session.get(Box, payload.box_id)
    return BoxOut.from_row(box)


@app.get("/api/boxes", response_model=list[BoxOut])
def list_boxes(
    station: str | None = None,
    supplier: str | None = None,
    state: BoxState | None = None,
    session: Session = Depends(get_session),
) -> list[BoxOut]:
    q = session.query(Box)
    if station:
        q = q.filter(Box.station_id == station)
    if state:
        q = q.filter(Box.state == state.value)
    if supplier:
        q = q.join(Supplier).filter(Supplier.supplier_name == supplier)
    return [BoxOut.from_row(b) for b in q.order_by(Box.last_seen.desc()).all()]


@app.patch("/api/boxes/{box_id}", response_model=BoxOut)
def update_box(
    box_id: str, payload: BoxUpdate, session: Session = Depends(get_session)
) -> BoxOut:
    box = session.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="box not found")
    box.status = payload.status.value
    session.commit()
    session.refresh(box)
    return BoxOut.from_row(box)


@app.get("/api/events", response_model=list[EventOut])
def list_events(
    limit: int = 100,
    box_id: str | None = None,
    session: Session = Depends(get_session),
) -> list[EventOut]:
    q = session.query(BoxEvent)
    if box_id:
        q = q.filter(BoxEvent.box_id == box_id)
    rows = q.order_by(BoxEvent.ts.desc(), BoxEvent.event_id.desc()).limit(
        max(1, min(limit, 1000))
    )
    return [EventOut.from_row(e) for e in rows]


# --------------------------------------------------------------------------
# Stations
# --------------------------------------------------------------------------


@app.get("/api/stations")
def list_stations(session: Session = Depends(get_session)) -> list[dict]:
    out = []
    for station in session.query(Station).order_by(Station.station_id).all():
        counts = dict(
            session.query(BoxEvent.direction, func.count(BoxEvent.event_id))
            .filter(BoxEvent.station_id == station.station_id)
            .group_by(BoxEvent.direction)
            .all()
        )
        out.append(
            {
                "station_id": station.station_id,
                "station_name": station.station_name,
                "role": station.role,
                "checked_in": counts.get(Direction.IN.value, 0),
                "checked_out": counts.get(Direction.OUT.value, 0),
            }
        )
    return out


@app.post("/api/stations")
def create_station(payload: StationIn, session: Session = Depends(get_session)) -> dict:
    station = get_or_create_station(session, payload.station_id, payload.role)
    if payload.station_name:
        station.station_name = payload.station_name
    station.role = payload.role.value
    session.commit()
    return {"station_id": station.station_id, "role": station.role}


@app.patch("/api/stations/{station_id}")
def set_station_role(
    station_id: str, payload: RoleUpdate, session: Session = Depends(get_session)
) -> dict:
    station = session.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="station not found")
    station.role = payload.role.value
    session.commit()
    return {"station_id": station.station_id, "role": station.role}


# --------------------------------------------------------------------------
# Video
# --------------------------------------------------------------------------


def _run_video(
    path: str, station_id: str, offset_minutes: float, cleanup: bool
) -> Callable[[Job], dict]:
    def work(job: Job) -> dict:
        session = SessionLocal()
        try:
            info = video.probe(path)
            job.detail = f"{info['frames']} frames at {info['fps']:.0f}fps"

            def progress(index: int, total: int) -> None:
                job.percent = (index / total * 100) if total else 0.0
                job.detail = f"frame {index} of {total or '?'}"

            base = datetime.utcnow() - timedelta(minutes=offset_minutes)
            result = video.process_video(
                path, station_id, session, base_ts=base, on_progress=progress
            )
            return {"station_id": station_id, **result.as_dict(), "video": info}
        finally:
            session.close()
            if cleanup:
                Path(path).unlink(missing_ok=True)

    return work


@app.post("/api/video/upload", status_code=202)
async def upload_video(
    file: UploadFile = File(...),
    station_id: str = Form(...),
    offset_minutes: float = Form(0.0),
) -> dict:
    """Accept a clip, process it in the background, return a job to poll."""
    suffix = Path(file.filename or "clip.mp4").suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    size = 0
    try:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"clips must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
            tmp.write(chunk)
    finally:
        tmp.close()

    if size == 0:
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="that file was empty")

    job = registry.start(
        "video", file.filename or "upload", _run_video(tmp.name, station_id, offset_minutes, True)
    )
    return {"job_id": job.id}


@app.post("/api/video/demo", status_code=202)
def process_demo_clip(clip: str, station_id: str, offset_minutes: float = 0.0) -> dict:
    """Process one of the bundled demo clips, by name."""
    path = (DEMO_DIR / clip).resolve()
    if not str(path).startswith(str(DEMO_DIR.resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"no demo clip named {clip!r}")
    job = registry.start("video", clip, _run_video(str(path), station_id, offset_minutes, False))
    return {"job_id": job.id}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return registry.list()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no job with that id")
    return job.snapshot()


# --------------------------------------------------------------------------
# Dashboard data
# --------------------------------------------------------------------------


@app.get("/api/dashboard")
def dashboard_stats(session: Session = Depends(get_session)) -> dict:
    total = session.query(func.count(Box.box_id)).scalar() or 0
    by_state = dict(
        session.query(Box.state, func.count(Box.box_id)).group_by(Box.state).all()
    )

    dwells = sorted(
        b.dwell_seconds
        for b in session.query(Box).filter(Box.checked_out_at.isnot(None)).all()
        if b.dwell_seconds is not None
    )

    def median(values: list[float]) -> float | None:
        """Dwell is skewed by the odd box that sits for days; the median is
        what a floor manager actually recognises as typical."""
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    by_supplier = []
    rows = (
        session.query(
            Supplier.supplier_name,
            func.count(Box.box_id),
            func.sum(case((Box.state == BoxState.IN_STOCK.value, 1), else_=0)),
        )
        .join(Box, Box.supplier_id == Supplier.supplier_id)
        .group_by(Supplier.supplier_name)
        .all()
    )
    for name, count, in_stock in rows:
        by_supplier.append(
            {"supplier": name, "total": int(count), "in_stock": int(in_stock or 0)}
        )
    by_supplier.sort(key=lambda r: -r["total"])

    # Crossings bucketed by the hour, so the dashboard can draw a flow chart.
    flow: OrderedDict[str, dict] = OrderedDict()
    for event in session.query(BoxEvent).order_by(BoxEvent.ts).all():
        bucket = event.ts.replace(minute=0, second=0, microsecond=0).isoformat()
        entry = flow.setdefault(bucket, {"bucket": bucket, "in": 0, "out": 0})
        entry[event.direction] += 1

    recent = [
        EventOut.from_row(e).model_dump(mode="json")
        for e in session.query(BoxEvent)
        .order_by(BoxEvent.ts.desc(), BoxEvent.event_id.desc())
        .limit(12)
        .all()
    ]

    return {
        "kpis": {
            "total_boxes": total,
            "in_stock": by_state.get(BoxState.IN_STOCK.value, 0),
            "departed": by_state.get(BoxState.DEPARTED.value, 0),
            "events": session.query(func.count(BoxEvent.event_id)).scalar() or 0,
            "median_dwell_s": median(dwells),
            "avg_dwell_s": (sum(dwells) / len(dwells)) if dwells else None,
            "completed_trips": len(dwells),
        },
        "by_supplier": by_supplier,
        "stations": list_stations(session),
        "flow": list(flow.values()),
        "recent": recent,
        "boxes": [
            BoxOut.from_row(b).model_dump(mode="json")
            for b in session.query(Box).order_by(Box.last_seen.desc()).all()
        ],
    }


@app.post("/api/demo/reset")
def reset_demo(session: Session = Depends(get_session)) -> dict:
    """Clear every box and crossing, so the demo can be replayed from scratch."""
    session.query(BoxEvent).delete()
    session.query(Box).delete()
    session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Dashboard page
# --------------------------------------------------------------------------

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host=cfg.api_host, port=cfg.api_port, reload=False)
