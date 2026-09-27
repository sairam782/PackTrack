"""The check-in / check-out rule. If this is wrong, the dashboard lies."""

from datetime import datetime, timedelta

import pytest

from db import BoxEvent, BoxState, Direction, StationRole
from tracking import get_or_create_station, is_repeat, record_scan, resolve_direction

T0 = datetime(2026, 9, 27, 9, 0, 0)


@pytest.mark.parametrize(
    "role,state,expected",
    [
        (StationRole.INBOUND, None, Direction.IN),
        (StationRole.INBOUND, BoxState.IN_STOCK, Direction.IN),
        (StationRole.OUTBOUND, BoxState.IN_STOCK, Direction.OUT),
        (StationRole.OUTBOUND, None, Direction.OUT),
        # A single camera doing both jobs flips the box each time.
        (StationRole.BOTH, BoxState.IN_STOCK, Direction.OUT),
        (StationRole.BOTH, BoxState.DEPARTED, Direction.IN),
    ],
)
def test_the_station_decides_the_direction(role, state, expected):
    assert resolve_direction(role, state) is expected


def test_repeat_detection_window():
    assert is_repeat(Direction.IN, T0, Direction.IN, T0 + timedelta(seconds=2))
    assert not is_repeat(Direction.IN, T0, Direction.IN, T0 + timedelta(seconds=30))
    assert not is_repeat(Direction.IN, T0, Direction.OUT, T0 + timedelta(seconds=2))
    assert not is_repeat(None, None, Direction.IN, T0)


def test_a_box_seen_in_many_frames_is_one_crossing(session):
    """A camera sees the same label thirty times; that is one arrival."""
    get_or_create_station(session, "IN", StationRole.INBOUND)
    for i in range(30):
        record_scan(session, box_id="B1", station_id="IN", ts=T0 + timedelta(seconds=i * 0.2))

    assert session.query(BoxEvent).count() == 1


def test_the_same_box_can_return_after_the_cooldown(session):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    record_scan(session, box_id="B1", station_id="IN", ts=T0)
    record_scan(session, box_id="B1", station_id="IN", ts=T0 + timedelta(minutes=5))

    assert session.query(BoxEvent).count() == 2


def test_in_then_out_sets_state_and_dwell(session):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    get_or_create_station(session, "OUT", StationRole.OUTBOUND)

    record_scan(session, box_id="B1", station_id="IN", ts=T0)
    box = session.get(__import__("db").Box, "B1")
    assert box.state == BoxState.IN_STOCK.value
    assert box.dwell_seconds is None

    record_scan(session, box_id="B1", station_id="OUT", ts=T0 + timedelta(hours=2))
    session.refresh(box)
    assert box.state == BoxState.DEPARTED.value
    assert box.dwell_seconds == pytest.approx(7200)


def test_a_returning_box_starts_a_fresh_stay(session):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    get_or_create_station(session, "OUT", StationRole.OUTBOUND)
    Box = __import__("db").Box

    record_scan(session, box_id="B1", station_id="IN", ts=T0)
    record_scan(session, box_id="B1", station_id="OUT", ts=T0 + timedelta(hours=1))
    record_scan(session, box_id="B1", station_id="IN", ts=T0 + timedelta(hours=2))

    box = session.get(Box, "B1")
    assert box.state == BoxState.IN_STOCK.value
    # The previous trip's checkout must not linger and report a bogus dwell.
    assert box.checked_out_at is None
    assert box.dwell_seconds is None


def test_out_at_one_station_is_not_suppressed_by_in_at_another(session):
    """The cooldown is per station, so a quick transfer still records both."""
    get_or_create_station(session, "IN", StationRole.INBOUND)
    get_or_create_station(session, "OUT", StationRole.OUTBOUND)

    record_scan(session, box_id="B1", station_id="IN", ts=T0)
    record_scan(session, box_id="B1", station_id="OUT", ts=T0 + timedelta(seconds=1))

    assert session.query(BoxEvent).count() == 2


def test_supplier_and_part_come_from_the_payload(session):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    record_scan(
        session, box_id="B1", station_id="IN", ts=T0,
        supplier_name="Acme Forge", part_type="bracket",
    )
    box = session.get(__import__("db").Box, "B1")
    assert box.supplier.supplier_name == "Acme Forge"
    assert box.part_type == "bracket"


def test_result_reports_whether_it_recorded(session):
    get_or_create_station(session, "IN", StationRole.INBOUND)
    first = record_scan(session, box_id="B1", station_id="IN", ts=T0)
    second = record_scan(session, box_id="B1", station_id="IN", ts=T0 + timedelta(seconds=1))

    assert first.recorded and first.direction is Direction.IN
    assert second.suppressed and "cooldown" in second.reason
