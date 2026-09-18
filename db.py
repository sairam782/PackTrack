from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, String, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from config import cfg
 

class BoxStatus(str, Enum):
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
    bbox_x = Column(Integer, nullable=True)
    bbox_y = Column(Integer, nullable=True)
    bbox_w = Column(Integer, nullable=True)
    bbox_h = Column(Integer, nullable=True)

    station = relationship("Station", back_populates="boxes")
    supplier = relationship("Supplier", back_populates="boxes")


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
