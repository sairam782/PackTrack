"""Camera observations and automatic placed -> collected transitions."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from db import Box, BoxStatus, Observation, Station, Supplier, get_session

router = APIRouter()


class ObservationIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)
    event_id: UUID
    box_id: str = Field(min_length=1, max_length=200, pattern=r"^[^/]+$")
    camera_id: str = Field(min_length=1, max_length=200)
    role: Literal["placement", "collection"]
    observed_at: datetime
    supplier_name: str | None = None
    part_type: str | None = None
    pixel_x: float = Field(ge=0)
    pixel_y: float = Field(ge=0)
    floor_x: float | None = None
    floor_y: float | None = None
    coordinate_frame: str | None = Field(default=None, min_length=1)
    calibration_id: str | None = Field(default=None, min_length=1)
    position_source: Literal["camera_calibration", "location_marker"] = "camera_calibration"
    location_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("observed_at")
    @classmethod
    def utc_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        value = value.astimezone(timezone.utc)
        if value > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError("observation is in the future; synchronize camera clocks")
        return value

    @model_validator(mode="after")
    def require_calibration(self):
        if self.position_source == "location_marker" and self.location_id is None:
            raise ValueError("location marker observations require location_id")
        fields = (self.floor_x, self.floor_y, self.coordinate_frame, self.calibration_id)
        if self.role == "placement" and any(v is None for v in fields):
            raise ValueError("placement requires calibrated floor coordinates, frame and calibration ID")
        if any(v is not None for v in fields) and any(v is None for v in fields):
            raise ValueError("supply all calibration fields together")
        return self


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    box_id: str
    camera_id: str
    role: str
    observed_at: datetime
    received_at: datetime
    pixel_x: float
    pixel_y: float
    floor_x: float | None
    floor_y: float | None
    coordinate_frame: str | None
    calibration_id: str | None
    position_source: str | None
    location_id: str | None
    applied: bool
    reason: str


def _result(event, box, duplicate=False):
    return {"event_id": event.event_id, "box_id": box.box_id,
            "applied": event.applied, "reason": event.reason,
            "duplicate": duplicate, "status": box.status}


@router.post("/observations")
def observe(payload: ObservationIn, session: Session = Depends(get_session)) -> dict:
    fingerprint = hashlib.sha256(json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    for attempt in range(4):
        try:
            return _apply(payload, fingerprint, session)
        except (IntegrityError, StaleDataError):
            # Versioned updates prevent simultaneous floor/truck workers from
            # overwriting collection. Retry the entire transaction with fresh state.
            session.rollback()
            if attempt == 3:
                raise HTTPException(409, "Concurrent camera update; retry this event ID")


def _apply(payload, fingerprint, session):
    event_id = str(payload.event_id)
    previous = session.get(Observation, event_id)
    if previous:
        if previous.payload_hash != fingerprint:
            raise HTTPException(409, "event_id was already used for different evidence")
        return _result(previous, session.get(Box, previous.box_id), duplicate=True)

    stamp = payload.observed_at.replace(tzinfo=None)
    box = session.get(Box, payload.box_id)
    if not session.get(Station, payload.camera_id):
        session.add(Station(station_id=payload.camera_id, station_name=payload.camera_id))
        session.flush()
    if box is None:
        box = Box(box_id=payload.box_id, station_id=payload.camera_id,
                  first_seen=stamp, last_seen=stamp, status=BoxStatus.ACTIVE.value)
        session.add(box)
        session.flush()

    applied, reason = True, payload.role
    if box.status == BoxStatus.COLLECTED.value:
        applied, reason = False, "already_collected"
    elif box.placed_at is not None and stamp <= box.placed_at:
        applied, reason = False, "out_of_order"

    if applied:
        if payload.supplier_name:
            supplier = session.query(Supplier).filter_by(supplier_name=payload.supplier_name).one_or_none()
            if supplier is None:
                supplier = Supplier(supplier_name=payload.supplier_name)
                session.add(supplier)
                session.flush()
            box.supplier_id = supplier.supplier_id
        if payload.part_type:
            box.part_type = payload.part_type
        box.last_seen = max(box.last_seen, stamp)
        if payload.role == "placement":
            box.station_id = payload.camera_id
            box.floor_x, box.floor_y = payload.floor_x, payload.floor_y
            box.coordinate_frame = payload.coordinate_frame
            box.calibration_id = payload.calibration_id
            box.position_source = payload.position_source
            box.location_id = payload.location_id
            box.placed_at = stamp
            next_status = BoxStatus.PLACED.value
        else:
            # Keep the last floor position; truck coordinates belong to a
            # different view. The collection camera/time are stored separately.
            box.collected_at = stamp
            box.collection_camera_id = payload.camera_id
            next_status = BoxStatus.COLLECTED.value
        if box.status != next_status:
            box.status = next_status
            box.status_updated_at = stamp

    event = Observation(
        event_id=event_id, box_id=payload.box_id, camera_id=payload.camera_id,
        role=payload.role, observed_at=stamp, pixel_x=payload.pixel_x, pixel_y=payload.pixel_y,
        floor_x=payload.floor_x, floor_y=payload.floor_y,
        coordinate_frame=payload.coordinate_frame, calibration_id=payload.calibration_id,
        position_source=payload.position_source, location_id=payload.location_id,
        applied=applied, reason=reason, payload_hash=fingerprint,
    )
    session.add(event)
    session.commit()
    return _result(event, box)


@router.get("/boxes/{box_id}/observations", response_model=list[ObservationOut])
def history(box_id: str, limit: int = Query(default=100, ge=1, le=1000),
            session: Session = Depends(get_session)):
    if session.get(Box, box_id) is None:
        raise HTTPException(404, "box not found")
    return (session.query(Observation).filter_by(box_id=box_id)
            .order_by(Observation.observed_at.desc(), Observation.event_id).limit(limit).all())
