from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from config import cfg


class BoxStatus(str, Enum):
    ACTIVE = "active"
    EMPTY = "empty"
    COLLECTED = "collected"


class Direction(str, Enum):
    """Which way a box crossed a station."""

    IN = "in"
    OUT = "out"


class StationRole(str, Enum):
    """What a station's scans mean.

    A scan carries no direction of its own, so the station supplies it: goods
    arriving are read at an inbound station, goods leaving at an outbound one.
    BOTH is for a single-camera setup, where the box's current state decides
    and each scan flips it.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class BoxState(str, Enum):
    """Where a box is right now, derived from its latest event."""

    IN_STOCK = "in_stock"
    DEPARTED = "departed"


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"

    supplier_id = Column(Integer, primary_key=True, autoincrement=True)
    supplier_name = Column(String, unique=True, nullable=False)
    contact_info = Column(String, nullable=True)

    boxes = relationship("Box", back_populates="supplier")


class Station(Base):
    __tablename__ = "stations"

    station_id = Column(String, primary_key=True)
    station_name = Column(String, nullable=False)
    camera_url = Column(String, nullable=True)
    role = Column(String, default=StationRole.INBOUND.value, nullable=False)

    boxes = relationship("Box", back_populates="station")
    events = relationship("BoxEvent", back_populates="station")


class Box(Base):
    __tablename__ = "boxes"

    box_id = Column(String, primary_key=True)
    station_id = Column(String, ForeignKey("stations.station_id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id"), nullable=True)
    part_type = Column(String, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, default=BoxStatus.ACTIVE.value, nullable=False)
    bbox_x = Column(Integer, nullable=True)
    bbox_y = Column(Integer, nullable=True)
    bbox_w = Column(Integer, nullable=True)
    bbox_h = Column(Integer, nullable=True)

    # Current whereabouts, kept in step with the latest event so the dashboard
    # does not have to re-derive it on every read.
    state = Column(String, default=BoxState.IN_STOCK.value, nullable=False)
    checked_in_at = Column(DateTime, nullable=True)
    checked_out_at = Column(DateTime, nullable=True)

    station = relationship("Station", back_populates="boxes")
    supplier = relationship("Supplier", back_populates="boxes")
    events = relationship(
        "BoxEvent", back_populates="box", order_by="BoxEvent.ts",
        cascade="all, delete-orphan",
    )

    @property
    def dwell_seconds(self) -> float | None:
        """How long the box stayed, for a completed in-then-out round trip."""
        if self.checked_in_at is None or self.checked_out_at is None:
            return None
        delta = (self.checked_out_at - self.checked_in_at).total_seconds()
        return delta if delta >= 0 else None


class BoxEvent(Base):
    """One crossing. The append-only record the dashboard is built on."""

    __tablename__ = "box_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    box_id = Column(String, ForeignKey("boxes.box_id"), nullable=False, index=True)
    station_id = Column(String, ForeignKey("stations.station_id"), nullable=False)
    direction = Column(String, nullable=False)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    source = Column(String, default="live", nullable=False)
    confidence = Column(Float, nullable=True)
    frame_index = Column(Integer, nullable=True)

    box = relationship("Box", back_populates="events")
    station = relationship("Station", back_populates="events")


engine = create_engine(
    cfg.db_url,
    connect_args={"check_same_thread": False} if cfg.db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
