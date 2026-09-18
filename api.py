from datetime import datetime
from typing import Optional
  
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import cfg
from db import Box, BoxStatus, Station, Supplier, get_session, init_db


class ScanIn(BaseModel):
    box_id: str
    station_id: str
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
            bbox=bbox,
        )


class BoxUpdate(BaseModel):
    status: BoxStatus


app = FastAPI(title="PackTrack API")


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Seed the configured stations so /scan doesn't fail on unknown FKs.
    session = next(get_session())
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


@app.post("/scan", response_model=BoxOut)
def scan(payload: ScanIn, session: Session = Depends(get_session)) -> BoxOut:
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
        )
        session.add(box)
    else:
        box.station_id = payload.station_id
        box.last_seen = now
        if supplier is not None:
            box.supplier_id = supplier.supplier_id
        if payload.part_type:
            box.part_type = payload.part_type

    if payload.bbox is not None:
        box.bbox_x, box.bbox_y, box.bbox_w, box.bbox_h = payload.bbox

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
    box.status = payload.status.value
    box.last_seen = datetime.utcnow()
    session.commit()
    session.refresh(box)
    return BoxOut.from_row(box)


@app.get("/dashboard")
def dashboard_stats(session: Session = Depends(get_session)) -> dict:
    total = session.query(func.count(Box.box_id)).scalar() or 0
    by_status = dict(
        session.query(Box.status, func.count(Box.box_id))
        .group_by(Box.status)
        .all()
    )
    by_station = dict(
        session.query(Box.station_id, func.count(Box.box_id))
        .group_by(Box.station_id)
        .all()
    )
    pickup_queue = (
        session.query(Supplier.supplier_name, func.count(Box.box_id))
        .join(Box, Box.supplier_id == Supplier.supplier_id)
        .filter(Box.status == BoxStatus.EMPTY.value)
        .group_by(Supplier.supplier_name)
        .all()
    )
    return {
        "total_boxes": total,
        "by_status": by_status,
        "by_station": by_station,
        "pickup_queue": [
            {"supplier": name, "empty_boxes": count} for name, count in pickup_queue
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host=cfg.api_host, port=cfg.api_port, reload=False)
