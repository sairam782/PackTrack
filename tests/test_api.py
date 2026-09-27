"""The HTTP surface. Registering a route is not the same as it working."""

import pytest
from conftest import DEMO


def test_health_reports_the_decoder_in_use(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["qr_backend"] in {"pyzbar", "opencv"}
    assert "qr_backend_note" in body


def test_stations_are_seeded_with_roles(client):
    roles = {s["station_id"]: s["role"] for s in client.get("/api/stations").json()}
    assert roles["STATION-IN"] == "inbound"
    assert roles["STATION-OUT"] == "outbound"


def test_a_scan_creates_a_crossing_in_the_right_direction(clean):
    clean.post(
        "/api/scan",
        json={"box_id": "T-1", "station_id": "STATION-IN", "supplier_name": "Acme"},
    )
    box = clean.get("/api/boxes").json()[0]
    assert box["box_id"] == "T-1"
    assert box["state"] == "in_stock"
    assert box["supplier_name"] == "Acme"

    events = clean.get("/api/events").json()
    assert [e["direction"] for e in events] == ["in"]


def test_checking_out_records_the_dwell(clean):
    clean.post("/api/scan", json={"box_id": "T-2", "station_id": "STATION-IN"})
    clean.post("/api/scan", json={"box_id": "T-2", "station_id": "STATION-OUT"})

    box = next(b for b in clean.get("/api/boxes").json() if b["box_id"] == "T-2")
    assert box["state"] == "departed"
    assert box["dwell_seconds"] is not None


def test_station_role_can_be_changed(client):
    client.post("/api/stations", json={"station_id": "FLEX", "role": "both"})
    changed = client.patch("/api/stations/FLEX", json={"role": "outbound"})
    assert changed.json()["role"] == "outbound"
    assert client.patch("/api/stations/NOPE", json={"role": "inbound"}).status_code == 404


def test_dashboard_has_everything_the_page_draws(clean):
    clean.post("/api/scan", json={"box_id": "T-3", "station_id": "STATION-IN"})
    data = clean.get("/api/dashboard").json()

    assert {"kpis", "by_supplier", "stations", "flow", "recent", "boxes"} <= set(data)
    assert data["kpis"]["in_stock"] == 1
    assert "median_dwell_s" in data["kpis"]
    assert data["recent"][0]["box_id"] == "T-3"


def test_unknown_job_and_clip_are_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    traversal = client.post("/api/video/demo?clip=../api.py&station_id=STATION-IN")
    assert traversal.status_code == 404
    assert client.post("/api/video/demo?clip=missing.mp4&station_id=STATION-IN").status_code == 404


def test_an_empty_upload_is_rejected(client):
    response = client.post(
        "/api/video/upload",
        files={"file": ("empty.mp4", b"", "video/mp4")},
        data={"station_id": "STATION-IN"},
    )
    assert response.status_code == 422


@pytest.mark.skipif(
    not (DEMO / "inbound.mp4").is_file(), reason="run tools/make_demo_video.py"
)
def test_processing_a_demo_clip_end_to_end(clean):
    import time

    created = clean.post(
        "/api/video/demo?clip=inbound.mp4&station_id=STATION-IN&offset_minutes=60"
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        snapshot = clean.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] != "running":
            break
        time.sleep(0.4)

    assert snapshot["status"] == "done", snapshot.get("error")
    assert snapshot["result"]["events"] == 6
    # Many reads of each label collapse into one crossing per box.
    assert snapshot["result"]["suppressed"] > snapshot["result"]["events"]

    assert clean.get("/api/dashboard").json()["kpis"]["in_stock"] == 6


def test_the_dashboard_page_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "PackTrack" in page.text
    assert client.get("/static/app.js").status_code == 200
