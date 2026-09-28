from contextlib import asynccontextmanager
import asyncio
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional
  
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from config import cfg
from db import (
    Box, BoxStatus, Observation, SessionLocal, Station, Supplier, get_session, init_db,
)


class ScanIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    box_id: str = Field(min_length=1, max_length=200, pattern=r"^[^/]+$")
    station_id: str = Field(min_length=1, max_length=200)
    supplier_name: Optional[str] = None
    part_type: Optional[str] = None
    bbox: Optional[tuple[int, int, int, int]] = Field(
        default=None, description="(x, y, w, h) in pixels"
    )


class BoxOut(BaseModel):
    box_id: str
    station_id: str
    supplier_name: Optional[str] = None
    part_type: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    status: str
    status_updated_at: Optional[datetime] = None
    is_stale: bool
    floor_x: Optional[float] = None
    floor_y: Optional[float] = None
    coordinate_frame: Optional[str] = None
    coordinate_units: str = "metres"
    calibration_id: Optional[str] = None
    position_source: Optional[str] = None
    location_id: Optional[str] = None
    placed_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    collection_camera_id: Optional[str] = None
    bbox: Optional[tuple[int, int, int, int]] = None

    @classmethod
    def from_row(cls, box: Box) -> "BoxOut":
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
            status_updated_at=box.status_updated_at,
            is_stale=box.last_seen < datetime.utcnow() - timedelta(seconds=cfg.stale_after_s),
            floor_x=box.floor_x, floor_y=box.floor_y,
            coordinate_frame=box.coordinate_frame, calibration_id=box.calibration_id,
            position_source=box.position_source, location_id=box.location_id,
            placed_at=box.placed_at, collected_at=box.collected_at,
            collection_camera_id=box.collection_camera_id,
            bbox=bbox,
        )


class BoxUpdate(BaseModel):
    status: BoxStatus


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    # Seed the configured stations so /scan doesn't fail on unknown FKs.
    session = SessionLocal()
    try:
        for station_id, cam in cfg.cameras.items():
            if not session.get(Station, station_id):
                session.add(Station(
                    station_id=station_id,
                    station_name=station_id,
                    camera_url=str(cam),
                ))
        session.commit()
    finally:
        session.close()
    application.state.demo = None
    task = None
    if cfg.demo_mode:
        from demo import DemoWorld
        application.state.demo = DemoWorld()
        await asyncio.to_thread(application.state.demo.tick)
        await asyncio.to_thread(application.state.demo.tick)
        async def refresh_demo():
            while True:
                await asyncio.sleep(2)
                await asyncio.to_thread(application.state.demo.tick)
        task = asyncio.create_task(refresh_demo())
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="PackTrack API", lifespan=lifespan)

# Camera-driven tracking API; legacy /scan remains available for older clients.
from observations import router as observations_router
app.include_router(observations_router)


def _get_or_create_supplier(session: Session, name: Optional[str]) -> Optional[Supplier]:
    if not name:
        return None
    supplier = session.query(Supplier).filter_by(supplier_name=name).one_or_none()
    if supplier is None:
        supplier = Supplier(supplier_name=name)
        session.add(supplier)
        session.flush()
    return supplier


def _get_or_create_station(session: Session, station_id: str) -> Station:
    station = session.get(Station, station_id)
    if station is None:
        station = Station(station_id=station_id, station_name=station_id)
        session.add(station)
        session.flush()
    return station


@app.get("/")
def index() -> dict:
    return {
        "service": "PackTrack API",
        "docs": "/docs",
        "endpoints": {
            "POST /scan": "log a decoded box",
            "GET /boxes": "list boxes (filters: station, supplier, status)",
            "PATCH /boxes/{box_id}": "update status",
            "GET /dashboard": "summary stats and supplier pickup queue",
        },
    }


@app.post("/scan", response_model=BoxOut)
def scan(payload: ScanIn, session: Session = Depends(get_session)) -> BoxOut:
    # Cameras may discover the same supplier/station/box concurrently.
    for attempt in range(3):
        try:
            return _save_scan(payload, session)
        except (IntegrityError, StaleDataError):
            session.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail="Concurrent scan; please retry")


def _save_scan(payload: ScanIn, session: Session) -> BoxOut:
    _get_or_create_station(session, payload.station_id)
    supplier = _get_or_create_supplier(session, payload.supplier_name)
    now = datetime.utcnow()

    box = session.get(Box, payload.box_id)
    if box is None:
        box = Box(
            box_id=payload.box_id,
            station_id=payload.station_id,
            supplier_id=supplier.supplier_id if supplier else None,
            part_type=payload.part_type,
            first_seen=now,
            last_seen=now,
            status=BoxStatus.ACTIVE.value,
            status_updated_at=now,
        )
        session.add(box)
    else:
        if box.calibration_id or box.collected_at:
            raise HTTPException(status_code=409, detail="Use /observations for camera-tracked boxes")
        box.station_id = payload.station_id
        box.last_seen = now
        if supplier is not None:
            box.supplier_id = supplier.supplier_id
        if payload.part_type:
            box.part_type = payload.part_type

    # Coordinates describe this sighting, never a previous station's frame.
    box.bbox_x, box.bbox_y, box.bbox_w, box.bbox_h = payload.bbox or (None,) * 4

    session.commit()
    session.refresh(box)
    return BoxOut.from_row(box)


@app.get("/boxes", response_model=list[BoxOut])
def list_boxes(
    station: Optional[str] = None,
    supplier: Optional[str] = None,
    status: Optional[BoxStatus] = None,
    session: Session = Depends(get_session),
) -> list[BoxOut]:
    q = session.query(Box)
    if station:
        q = q.filter(Box.station_id == station)
    if status:
        q = q.filter(Box.status == status.value)
    if supplier:
        q = q.join(Supplier).filter(Supplier.supplier_name == supplier)
    return [BoxOut.from_row(b) for b in q.order_by(Box.last_seen.desc()).all()]


@app.patch("/boxes/{box_id}", response_model=BoxOut)
def update_box(
    box_id: str,
    payload: BoxUpdate,
    session: Session = Depends(get_session),
) -> BoxOut:
    box = session.get(Box, box_id)
    if box is None:
        raise HTTPException(status_code=404, detail="box not found")
    if box.calibration_id or box.collected_at:
        raise HTTPException(status_code=409, detail="Camera-tracked status is updated through /observations")
    if box.status != payload.status.value:
        box.status = payload.status.value
        box.status_updated_at = datetime.utcnow()
    session.commit()
    session.refresh(box)
    return BoxOut.from_row(box)


@app.get("/dashboard")
def dashboard_stats(session: Session = Depends(get_session)) -> dict:
    boxes = session.query(Box).all()
    cutoff = datetime.utcnow() - timedelta(seconds=cfg.stale_after_s)
    by_status, by_station, queue = {}, {}, {}
    stale_count = present_count = ready_count = 0
    for box in boxes:
        by_status[box.status] = by_status.get(box.status, 0) + 1
        if box.status == BoxStatus.COLLECTED.value:
            continue
        if box.last_seen < cutoff:
            stale_count += 1
            continue
        present_count += 1
        by_station[box.station_id] = by_station.get(box.station_id, 0) + 1
        if box.status == BoxStatus.EMPTY.value:
            ready_count += 1
            supplier = box.supplier.supplier_name if box.supplier else None
            key = (supplier, box.station_id)
            group = queue.setdefault(key, {
                "supplier": supplier, "station": box.station_id,
                "empty_boxes": 0, "box_ids": [],
            })
            group["empty_boxes"] += 1
            group["box_ids"].append(box.box_id)
    for group in queue.values():
        group["box_ids"].sort()
    suppliers = {}
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    hours = Counter(b.collected_at.replace(minute=0, second=0, microsecond=0)
                    for b in boxes if b.collected_at and b.collected_at >= today)
    for box in boxes:
        name = box.supplier.supplier_name if box.supplier else "Unknown supplier"
        group = suppliers.setdefault(name, {"supplier": name, "on_floor": 0, "collected": 0, "stale": 0})
        key = "collected" if box.status == "collected" else "stale" if box.last_seen < cutoff else "on_floor"
        group[key] += 1
    return {
        "demo_mode": cfg.demo_mode,
        "auto_collect": bool(getattr(getattr(app.state, "demo", None), "auto_collect", False)),
        "collected_today": sum(hours.values()),
        "supplier_summary": sorted(suppliers.values(), key=lambda g: g["supplier"]),
        "collection_timeline": [{"hour": (today + timedelta(hours=h)).isoformat(),
                                 "boxes": hours[today + timedelta(hours=h)]}
                                for h in range(datetime.utcnow().hour + 1)],
        "total_boxes": len(boxes),
        "present_boxes": present_count,
        "ready_boxes": ready_count,
        "stale_boxes": stale_count,
        "stale_after_seconds": cfg.stale_after_s,
        "by_status": by_status,
        "by_station": by_station,
        "pickup_queue": sorted(queue.values(), key=lambda g: (g["supplier"] or "", g["station"])),
    }


@app.get("/activity")
def activity(limit: int = Query(default=30, ge=1, le=200), session: Session = Depends(get_session)):
    rows = session.query(Observation).order_by(Observation.received_at.desc()).limit(limit).all()
    return [{"event_id": e.event_id, "box_id": e.box_id, "camera_id": e.camera_id,
             "event": e.role, "observed_at": e.observed_at, "applied": e.applied,
             "reason": e.reason, "floor_x": e.floor_x, "floor_y": e.floor_y} for e in rows]


@app.get("/phone-connection")
def phone_connection():
    url, token = os.getenv("PACKTRACK_PHONE_URL"), os.getenv("PACKTRACK_PHONE_TOKEN")
    return {"enabled": bool(url and token), "url": f"{url}#token={token}" if url and token else None}


def demo_world():
    world = getattr(app.state, "demo", None)
    if not cfg.demo_mode or world is None:
        raise HTTPException(404, "Demo controls are disabled")
    return world


class DemoCollect(BaseModel):
    box_id: str


class DemoAutomation(BaseModel):
    enabled: bool


@app.post("/demo/collect")
def demo_collect(payload: DemoCollect):
    try:
        return demo_world().collect(payload.box_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/demo/automation")
def demo_automation(payload: DemoAutomation):
    world = demo_world()
    with world.lock:
        world.auto_collect = payload.enabled
    return {"enabled": world.auto_collect}


@app.post("/demo/batch")
def demo_batch():
    try:
        return demo_world().add_batch()
    except ValueError as exc:
        raise HTTPException(409, str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=cfg.api_host, port=cfg.api_port, reload=False)
