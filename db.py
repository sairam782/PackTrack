from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column, DateTime, Float, Boolean, ForeignKey, Integer, String, create_engine, inspect, text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from config import cfg
 

class BoxStatus(str, Enum):
    PLACED = "placed"
    ACTIVE = "active"
    EMPTY = "empty"
    COLLECTED = "collected"


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

    boxes = relationship("Box", back_populates="station")


class Box(Base):
    __tablename__ = "boxes"

    box_id = Column(String, primary_key=True)
    station_id = Column(String, ForeignKey("stations.station_id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id"), nullable=True)
    part_type = Column(String, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    status = Column(String, default=BoxStatus.ACTIVE.value, nullable=False)
    status_updated_at = Column(DateTime, nullable=True)
    floor_x = Column(Float, nullable=True)
    floor_y = Column(Float, nullable=True)
    coordinate_frame = Column(String, nullable=True)
    calibration_id = Column(String, nullable=True)
    position_source = Column(String, nullable=True)
    location_id = Column(String, nullable=True)
    placed_at = Column(DateTime, nullable=True)
    collected_at = Column(DateTime, nullable=True)
    collection_camera_id = Column(String, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": version}
    bbox_x = Column(Integer, nullable=True)
    bbox_y = Column(Integer, nullable=True)
    bbox_w = Column(Integer, nullable=True)
    bbox_h = Column(Integer, nullable=True)

    station = relationship("Station", back_populates="boxes")
    supplier = relationship("Supplier", back_populates="boxes")


class Observation(Base):
    """Immutable camera evidence, including late/ignored observations."""
    __tablename__ = "observations"

    event_id = Column(String, primary_key=True)
    box_id = Column(String, ForeignKey("boxes.box_id"), nullable=False, index=True)
    camera_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    observed_at = Column(DateTime, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    pixel_x = Column(Float, nullable=False)
    pixel_y = Column(Float, nullable=False)
    floor_x = Column(Float, nullable=True)
    floor_y = Column(Float, nullable=True)
    coordinate_frame = Column(String, nullable=True)
    calibration_id = Column(String, nullable=True)
    position_source = Column(String, nullable=True)
    location_id = Column(String, nullable=True)
    applied = Column(Boolean, nullable=False)
    reason = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)


engine = create_engine(
    cfg.db_url,
    connect_args={"check_same_thread": False} if cfg.db_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    # Additive upgrade for existing prototype databases; preserve all box data.
    # Historical status-change times are unknown and intentionally stay NULL.
    columns = {c["name"] for c in inspect(engine).get_columns("boxes")}
    additions = {
        "status_updated_at": "TIMESTAMP", "floor_x": "FLOAT", "floor_y": "FLOAT",
        "coordinate_frame": "VARCHAR", "calibration_id": "VARCHAR", "placed_at": "TIMESTAMP",
        "collected_at": "TIMESTAMP", "collection_camera_id": "VARCHAR",
        "version": "INTEGER NOT NULL DEFAULT 1",
        "position_source": "VARCHAR", "location_id": "VARCHAR",
    }
    with engine.begin() as conn:
        for name, sql_type in additions.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE boxes ADD COLUMN {name} {sql_type}"))
    observation_columns = {c["name"] for c in inspect(engine).get_columns("observations")}
    with engine.begin() as conn:
        for name in ("position_source", "location_id"):
            if name not in observation_columns:
                conn.execute(text(f"ALTER TABLE observations ADD COLUMN {name} VARCHAR"))


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
