"""Reading the bundled clips end to end."""

import json

import pytest
from conftest import DEMO

from db import Box, BoxEvent, BoxState, Direction, StationRole
from tracking import get_or_create_station
from video import probe, process_video

pytestmark = pytest.mark.skipif(
    not (DEMO / "manifest.json").is_file(),
    reason="demo clips not generated; run tools/make_demo_video.py",
)


@pytest.fixture
def manifest():
    return json.loads((DEMO / "manifest.json").read_text())


def test_probe_reports_the_clip(manifest):
    info = probe(str(DEMO / manifest["inbound"]["file"]))
    assert info["frames"] > 0 and info["fps"] > 0
    assert info["width"] == 1280


def test_a_missing_file_is_a_clear_error():
    with pytest.raises(FileNotFoundError):
        probe("demo/does-not-exist.mp4")


def test_inbound_clip_checks_every_box_in(session, manifest):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    result = process_video(str(DEMO / manifest["inbound"]["file"]), "IN", session)

    expected = manifest["inbound"]["box_ids"]
    assert sorted(result.boxes) == sorted(expected)
    # Many frames of each box, collapsed to one crossing apiece.
    assert result.events == len(expected)
    assert result.suppressed > result.events
    assert session.query(BoxEvent).filter_by(direction=Direction.IN.value).count() == len(expected)


def test_a_full_round_trip_leaves_the_right_boxes_on_the_floor(session, manifest):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    get_or_create_station(session, "OUT", StationRole.OUTBOUND)

    process_video(str(DEMO / manifest["inbound"]["file"]), "IN", session)
    process_video(str(DEMO / manifest["outbound"]["file"]), "OUT", session)

    arrived = set(manifest["inbound"]["box_ids"])
    departed = set(manifest["outbound"]["box_ids"])

    on_floor = {
        b.box_id for b in session.query(Box).filter_by(state=BoxState.IN_STOCK.value)
    }
    assert on_floor == arrived - departed

    gone = session.query(Box).filter_by(state=BoxState.DEPARTED.value).all()
    assert {b.box_id for b in gone} == departed
    assert all(b.dwell_seconds is not None and b.dwell_seconds > 0 for b in gone)


def test_event_times_follow_the_footage_not_the_clock(session, manifest):
    """Timestamps come from frame position, so dwell measures the video."""
    get_or_create_station(session, "IN", StationRole.INBOUND)
    process_video(str(DEMO / manifest["inbound"]["file"]), "IN", session)

    times = [e.ts for e in session.query(BoxEvent).order_by(BoxEvent.ts).all()]
    span = (times[-1] - times[0]).total_seconds()
    clip_seconds = probe(str(DEMO / manifest["inbound"]["file"]))["duration_s"]
    assert 0 < span <= clip_seconds + 1
