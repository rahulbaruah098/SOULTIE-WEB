"""Permanent pure capacity-evaluation tests for Xuoroni cafes."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services.cafe_reservation_capacity_service import (
    CafeCapacityError,
    MAX_CAPACITY_ROWS,
    evaluate_reservation_capacity,
)


START = datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc)


def booking(
    offset=0,
    party=2,
    duration=60,
    buffer=15,
    status="confirmed",
):
    return {
        "start_at": START + timedelta(minutes=offset),
        "party_size": party,
        "duration_minutes": duration,
        "buffer_minutes": buffer,
        "status": status,
    }


def evaluate(
    reservations=(),
    *,
    party=4,
    seats=10,
    duration=60,
    buffer=15,
    maximum=None,
    start=START,
):
    return evaluate_reservation_capacity(
        start_at=start,
        party_size=party,
        duration_minutes=duration,
        buffer_minutes=buffer,
        seat_capacity=seats,
        max_concurrent_bookings=maximum,
        reservations=reservations,
    )


def test_empty_slot():
    result = evaluate([])
    assert result["available"] is True
    assert result["peak_occupied_seats"] == 4
    assert result["peak_concurrent_bookings"] == 1
    assert result["overlapping_reservations"] == 0
    assert result["end_at"] == START + timedelta(minutes=75)


def test_exact_seat_limit():
    result = evaluate([booking(party=6)])
    assert result["available"] is True
    assert result["peak_occupied_seats"] == 10


def test_overcapacity():
    result = evaluate([booking(party=7)])
    assert result["available"] is False
    assert result["peak_occupied_seats"] == 11


def test_pending_reservation_counts():
    result = evaluate([booking(party=7, status="pending")])
    assert result["available"] is False


@pytest.mark.parametrize(
    "offset,expected_overlap",
    [
        (-75, 0),
        (-74, 1),
        (-1, 1),
        (0, 1),
        (74, 1),
        (75, 0),
        (120, 0),
    ],
)
def test_half_open_interval_boundaries(
    offset, expected_overlap
):
    result = evaluate([booking(offset=offset)])
    assert (
        result["overlapping_reservations"]
        == expected_overlap
    )


def test_nonoverlapping_bookings_not_added_to_capacity():
    result = evaluate([
        booking(offset=-75, party=100),
        booking(offset=75, party=100),
    ])
    assert result["available"] is True
    assert result["overlapping_reservations"] == 0
    assert result["peak_occupied_seats"] == 4


def test_simultaneous_requests_consume_full_capacity():
    rows = [
        booking(offset=0, party=3),
        booking(offset=0, party=4),
    ]
    result = evaluate(rows)
    assert result["available"] is False
    assert result["peak_occupied_seats"] == 11
    assert result["peak_concurrent_bookings"] == 3


def test_disjoint_existing_bookings_use_peak_not_sum():
    # The two existing bookings do not overlap each other.
    # Each overlaps the requested 120-minute reservation.
    rows = [
        booking(offset=0, party=6, duration=45, buffer=0),
        booking(offset=60, party=6, duration=45, buffer=0),
    ]
    result = evaluate(
        rows, party=4, duration=120, buffer=0
    )
    assert result["available"] is True
    assert result["peak_occupied_seats"] == 10
    assert result["peak_concurrent_bookings"] == 2


def test_staggered_bookings_can_exceed_capacity():
    rows = [
        booking(offset=-30, party=3),
        booking(offset=20, party=5),
    ]
    result = evaluate(rows)
    assert result["available"] is False
    assert result["peak_occupied_seats"] == 12


def test_booking_buffer_changes_overlap():
    existing = booking(
        offset=-60, party=8, duration=60, buffer=15
    )
    assert evaluate([existing])["available"] is False

    existing["buffer_minutes"] = 0
    assert evaluate([existing])["available"] is True


def test_request_buffer_extends_interval():
    existing = booking(
        offset=70, party=8, duration=60, buffer=0
    )
    assert evaluate([existing], buffer=15)["available"] is False
    assert evaluate([existing], buffer=0)["available"] is True


def test_concurrency_limit():
    rows = [booking(party=1), booking(party=1)]
    result = evaluate(
        rows, party=1, maximum=2
    )
    assert result["available"] is False
    assert result["peak_concurrent_bookings"] == 3


def test_concurrency_limit_at_boundary():
    result = evaluate(
        [booking(party=1)],
        party=1,
        maximum=2,
    )
    assert result["available"] is True


def test_single_seat_capacity():
    result = evaluate(
        [], party=1, seats=1, maximum=1
    )
    assert result["available"] is True


def test_request_over_venue_capacity():
    result = evaluate([], party=8, seats=6)
    assert result["available"] is False


def test_request_timezone_normalized_to_utc():
    local = START.astimezone(
        timezone(timedelta(hours=5, minutes=30))
    )
    result = evaluate([], start=local)
    assert result["start_at"] == START


def test_existing_timestamp_timezone_normalized():
    existing = booking(party=7)
    existing["start_at"] = START.astimezone(
        timezone(timedelta(hours=5, minutes=30))
    )
    assert evaluate([existing])["available"] is False


def test_input_not_mutated():
    rows = [booking(), booking(offset=30)]
    before = deepcopy(rows)
    evaluate(rows)
    assert rows == before


def test_tuple_snapshot_is_accepted():
    result = evaluate((booking(party=6),))
    assert result["available"] is True


@pytest.mark.parametrize(
    "snapshot",
    [None, {}, "invalid", 1, True],
)
def test_snapshot_must_be_list_or_tuple(snapshot):
    with pytest.raises(CafeCapacityError):
        evaluate(snapshot)


def test_maximum_snapshot_rows_accepted():
    # Nonoverlapping records; limit is about snapshot size.
    rows = [
        booking(offset=1000 + i * 100)
        for i in range(MAX_CAPACITY_ROWS)
    ]
    assert evaluate(rows)["available"] is True


def test_snapshot_overflow_fails_closed():
    rows = [
        booking(offset=1000 + i * 100)
        for i in range(MAX_CAPACITY_ROWS + 1)
    ]
    with pytest.raises(CafeCapacityError):
        evaluate(rows)


@pytest.mark.parametrize(
    "invalid",
    [
        None, [], "invalid", {},
        booking(status="cancelled"),
        booking(status="rejected"),
        booking(status="completed"),
        booking(status="no_show"),
        {**booking(), "party_size": True},
        {**booking(), "party_size": 0},
        {**booking(), "duration_minutes": 0},
        {**booking(), "buffer_minutes": -1},
        {**booking(), "start_at": "2026-12-01"},
        {**booking(), "start_at": datetime(2026, 12, 1)},
    ],
)
def test_invalid_existing_record_fails_closed(invalid):
    with pytest.raises(CafeCapacityError):
        evaluate([invalid])


@pytest.mark.parametrize(
    "party",
    [None, True, False, 0, -1, 101, 2.5, "4"],
)
def test_invalid_requested_party_size(party):
    with pytest.raises(CafeCapacityError):
        evaluate([], party=party)


@pytest.mark.parametrize(
    "seats",
    [None, True, False, 0, -1, 10001, "20"],
)
def test_invalid_seat_capacity(seats):
    with pytest.raises(CafeCapacityError):
        evaluate([], seats=seats)


@pytest.mark.parametrize(
    "duration",
    [None, True, 0, 14, 721, "60"],
)
def test_invalid_duration(duration):
    with pytest.raises(CafeCapacityError):
        evaluate([], duration=duration)


@pytest.mark.parametrize(
    "buffer",
    [None, True, -1, 181, "15"],
)
def test_invalid_buffer(buffer):
    with pytest.raises(CafeCapacityError):
        evaluate([], buffer=buffer)


@pytest.mark.parametrize(
    "maximum",
    [True, False, 0, -1, 10001, "2"],
)
def test_invalid_max_concurrent(maximum):
    with pytest.raises(CafeCapacityError):
        evaluate([], maximum=maximum)


@pytest.mark.parametrize(
    "start",
    [None, "2026-12-01", 123, datetime(2026, 12, 1)],
)
def test_invalid_requested_start(start):
    with pytest.raises(CafeCapacityError):
        evaluate([], start=start)


def test_no_booking_or_database_side_effect():
    result = evaluate([])
    assert result["available"] is True
    assert "booking_id" not in result
    assert "reservation_id" not in result
    assert "confirmed" not in result
    assert "hold_token" not in result
