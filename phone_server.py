"""Restricted LAN gateway; application API and dashboard stay on localhost."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import secrets
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from phone_scanner import PhoneScanner, ScanError

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="PackTrack phone scanner", docs_url=None, redoc_url=None, openapi_url=None)
scanner = None


def authenticated(x_packtrack_token: str = Header(default="")):
    expected = os.getenv("PACKTRACK_PHONE_TOKEN", "")
    if not expected or not secrets.compare_digest(expected, x_packtrack_token):
        raise HTTPException(401, "Pair this phone using the link shown on the dashboard")


def service():
    global scanner
    if scanner is None:
        scanner = PhoneScanner(os.environ["PACKTRACK_PHONE_LOCATIONS"], os.environ["PACKTRACK_API_BASE_URL"])
    return scanner


class FrameIn(BaseModel):
    request_id: UUID
    device_id: UUID
    image: str = Field(min_length=1, max_length=8_000_000)
    location_token: str | None = None
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def valid_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("capture timestamp needs a timezone")
        value = value.astimezone(timezone.utc)
        if value > datetime.now(timezone.utc) + timedelta(seconds=30):
            raise ValueError("phone clock is ahead; synchronize its time")
        return value


@app.get("/phone")
def page():
    return FileResponse(ROOT / "phone/index.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/phone/app.js")
def script():
    return FileResponse(ROOT / "phone/app.js", media_type="text/javascript")


@app.get("/phone/config", dependencies=[Depends(authenticated)])
def configuration():
    return service().description()


@app.post("/phone/scan", dependencies=[Depends(authenticated)])
def scan(payload: FrameIn):
    try:
        return service().scan(str(payload.request_id), str(payload.device_id), payload.image,
                              payload.location_token, payload.captured_at)
    except ScanError as exc:
        raise HTTPException(422, str(exc))


@app.get("/health")
def health():
    return {"service": "PackTrack phone scanner"}
