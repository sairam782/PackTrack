"""Turning scans into crossings.

A QR read says only "this box was visible here". It does not say whether the
box was arriving or leaving. The station supplies that: a scan at an inbound
station checks the box in, a scan at an outbound station checks it out. A
station marked BOTH is a single-camera setup, where the box's current state
decides and each crossing flips it.

The other half of the job is that a camera sees the same label in many
consecutive frames. One box carried past a lens is one crossing, not thirty,
so a repeat of the same box in the same direction inside the cooldown window
is the same physical event and is dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from db import Box, BoxEvent, BoxState, Direction, Station, StationRole, Supplier

log = logging.getLogger(__name__)

# How long the same box at the same station is treated as still being the same
# crossing. Comfortably longer than a box takes to pass a camera, comfortably
# shorter than a box could plausibly leave and come back.
DEFAULT_COOLDOWN_S = 8.0


@dataclass
class ScanResult:
    box_id: str
    direction: Direction | None
    recorded: bool
    reason: str

    @property
    def suppressed(self) -> bool:
        return not self.recorded


def resolve_direction(role: StationRole, state: BoxState | None) -> Direction:
    """Which way this scan counts, given where it happened."""
    if role is StationRole.INBOUND:
        return Direction.IN
    if role is StationRole.OUTBOUND:
        return Direction.OUT
    # BOTH: one camera doing double duty, so each crossing flips the box.
    return Direction.OUT if state is BoxState.IN_STOCK else Direction.IN


def is_repeat(
    last_direction: Direction | None,
    last_ts: datetime | None,
    new_direction: Direction,
    new_ts: datetime,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
) -> bool:
    """True when this scan is the same crossing seen again."""
    if last_direction is None or last_ts is None:
        return False
    if last_direction is not new_direction:
        return False
    return abs((new_ts - last_ts).total_seconds()) < cooldown_s


def _latest_event(session: Session, box_id: str, station_id: str) -> BoxEvent | None:
    return (
        session.query(BoxEvent)
        .filter(BoxEvent.box_id == box_id, BoxEvent.station_id == station_id)
        .order_by(desc(BoxEvent.ts), desc(BoxEvent.event_id))
        .first()
    )


def get_or_create_station(
    session: Session, station_id: str, role: StationRole = StationRole.INBOUND
) -> Station:
    station = session.get(Station, station_id)
    if station is None:
        station = Station(
            station_id=station_id, station_name=station_id, role=role.value
        )
        session.add(station)
        session.flush()
    return station


def get_or_create_supplier(session: Session, name: str | None) -> Supplier | None:
    if not name:
        return None
    supplier = session.query(Supplier).filter_by(supplier_name=name).one_or_none()
    if supplier is None:
        supplier = Supplier(supplier_name=name)
        session.add(supplier)
        session.flush()
    return supplier


def record_scan(
    session: Session,
    *,
    box_id: str,
    station_id: str,
    ts: datetime | None = None,
    supplier_name: str | None = None,
    part_type: str | None = None,
    bbox: tuple[int, int, int, int] | None = None,
    source: str = "live",
    confidence: float | None = None,
    frame_index: int | None = None,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
) -> ScanResult:
    """Apply one scan: resolve its direction, drop repeats, record the rest."""
    ts = ts or datetime.utcnow()
    station = get_or_create_station(session, station_id)
    role = StationRole(station.role)

    box = session.get(Box, box_id)
    if box is None:
        supplier = get_or_create_supplier(session, supplier_name)
        box = Box(
            box_id=box_id,
            station_id=station_id,
            supplier_id=supplier.supplier_id if supplier else None,
            part_type=part_type,
            first_seen=ts,
            last_seen=ts,
            status=Box.__table__.c.status.default.arg,
            state=BoxState.IN_STOCK.value,
        )
        session.add(box)
        session.flush()
    else:
        if supplier_name:
            supplier = get_or_create_supplier(session, supplier_name)
            if supplier is not None:
                box.supplier_id = supplier.supplier_id
        if part_type:
            box.part_type = part_type

    direction = resolve_direction(role, BoxState(box.state))

    previous = _latest_event(session, box_id, station_id)
    if previous is not None and is_repeat(
        Direction(previous.direction), previous.ts, direction, ts, cooldown_s
    ):
        # Still the same crossing; refresh liveness but add no event.
        box.last_seen = max(box.last_seen or ts, ts)
        session.commit()
        return ScanResult(box_id, direction, False, "same crossing, within cooldown")

    session.add(
        BoxEvent(
            box_id=box_id,
            station_id=station_id,
            direction=direction.value,
            ts=ts,
            source=source,
            confidence=confidence,
            frame_index=frame_index,
        )
    )

    box.station_id = station_id
    box.last_seen = ts
    if bbox is not None:
        box.bbox_x, box.bbox_y, box.bbox_w, box.bbox_h = bbox

    if direction is Direction.IN:
        box.state = BoxState.IN_STOCK.value
        box.checked_in_at = ts
        # A box coming back starts a fresh stay.
        box.checked_out_at = None
    else:
        box.state = BoxState.DEPARTED.value
        box.checked_out_at = ts

    session.commit()
    return ScanResult(box_id, direction, True, f"checked {direction.value}")
