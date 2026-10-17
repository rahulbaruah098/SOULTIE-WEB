"""Venue-scoped, bounded reservation read repository security tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import PyMongoError
import pytest

from app.repositories import reservation_repository as repository


VENUE = "507f191e810c19729de860ea"
OTHER_VENUE = "507f191e810c19729de860bb"
START = datetime(2026, 12, 1, tzinfo=timezone.utc)
END = datetime(2026, 12, 2, tzinfo=timezone.utc)


def record(
    *,
    venue=VENUE,
    status="confirmed",
    start=None,
):
    if start is None:
        start = START + timedelta(hours=13)
    return {
        "_id": ObjectId(),
        "venue_id": ObjectId(venue),
        "status": status,
        "start_at": start,
    }


@pytest.fixture
def context(monkeypatch):
    cursor = Mock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = []
    collection = Mock()
    collection.find.return_value = cursor

    monkeypatch.setattr(
        repository,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(reservations=collection)
        ),
    )

    return SimpleNamespace(
        collection=collection,
        cursor=cursor,
    )


def lookup(
    *,
    venue=VENUE,
    start=START,
    end=END,
):
    return repository.find_active_reservation_starts(
        venue_id=venue,
        start_at=start,
        end_at=end,
    )


def set_rows(context, rows):
    context.cursor.limit.return_value = rows


def test_empty_window_returns_empty_list(context):
    assert lookup() == []
    context.collection.find.assert_called_once_with({
        "venue_id": ObjectId(VENUE),
        "status": {"$in": ["confirmed", "pending"]},
        "start_at": {"$gte": START, "$lt": END},
    })
    context.cursor.sort.assert_called_once_with([
        ("start_at", 1), ("_id", 1)
    ])
    context.cursor.limit.assert_called_once_with(501)


def test_pending_and_confirmed_rows_accepted(context):
    rows = [
        record(status="pending"),
        record(status="confirmed"),
    ]
    set_rows(context, rows)
    assert lookup() == rows


def test_start_boundary_inclusive(context):
    rows = [record(start=START)]
    set_rows(context, rows)
    assert lookup() == rows


def test_last_in_window_instant_accepted(context):
    rows = [record(start=END - timedelta(microseconds=1))]
    set_rows(context, rows)
    assert lookup() == rows


def test_different_venue_is_scoped(context):
    lookup(venue=OTHER_VENUE)
    query = context.collection.find.call_args.args[0]
    assert query["venue_id"] == ObjectId(OTHER_VENUE)
    assert query["venue_id"] != ObjectId(VENUE)


@pytest.mark.parametrize(
    "venue",
    [None, "", "bad", 123, True, [], {}, "f" * 25],
)
def test_invalid_venue_rejected_before_query(context, venue):
    with pytest.raises(repository.ReservationRepositoryError):
        lookup(venue=venue)
    context.collection.find.assert_not_called()


@pytest.mark.parametrize(
    "start,end",
    [
        (END, START),
        (START, START),
        (None, END),
        (START, None),
        ("2026-12-01", END),
        (START, "2026-12-02"),
        (datetime(2026, 12, 1), END),
        (START, datetime(2026, 12, 2)),
        (True, END),
        (START, 123),
    ],
)
def test_invalid_time_window_rejected_before_query(
    context, start, end
):
    with pytest.raises(repository.ReservationRepositoryError):
        lookup(start=start, end=end)
    context.collection.find.assert_not_called()


def test_utc_conversion_of_query_range(context):
    ist = timezone(timedelta(hours=5, minutes=30))
    local_start = START.astimezone(ist)
    local_end = END.astimezone(ist)

    assert lookup(start=local_start, end=local_end) == []
    query = context.collection.find.call_args.args[0]
    assert query["start_at"] == {
        "$gte": START,
        "$lt": END,
    }


def test_exact_one_year_window_allowed(context):
    assert lookup(
        end=START + timedelta(days=366)
    ) == []


def test_more_than_366_days_rejected(context):
    with pytest.raises(repository.ReservationRepositoryError):
        lookup(end=START + timedelta(days=367))
    context.collection.find.assert_not_called()


@pytest.mark.parametrize(
    "invalid_row",
    [
        None,
        [],
        "invalid",
        {},
        record(venue=OTHER_VENUE),
        record(status="cancelled"),
        record(status="completed"),
        record(start=START - timedelta(seconds=1)),
        record(start=END),
        record(start=datetime(2026, 12, 1, 13)),
        record(start="2026-12-01T13:00:00Z"),
    ],
)
def test_invalid_database_rows_rejected(context, invalid_row):
    set_rows(context, [invalid_row])
    with pytest.raises(repository.ReservationRepositoryError):
        lookup()


def test_maximum_result_count_accepted(context):
    rows = [record() for _ in range(500)]
    set_rows(context, rows)
    assert len(lookup()) == 500


def test_overflow_result_count_rejected(context):
    set_rows(context, [record() for _ in range(501)])
    with pytest.raises(repository.ReservationRepositoryError):
        lookup()


def test_database_failure_propagates(context):
    context.collection.find.side_effect = PyMongoError(
        "simulated database outage"
    )
    with pytest.raises(PyMongoError):
        lookup()


def test_repository_does_not_create_or_mutate_bookings(context):
    lookup()
    context.collection.insert_one.assert_not_called()
    context.collection.update_one.assert_not_called()
    context.collection.delete_one.assert_not_called()
    context.collection.find_one_and_update.assert_not_called()


def test_read_result_does_not_claim_availability(context):
    result = lookup()
    assert result == []
    assert not hasattr(repository, "create_reservation")
    assert not hasattr(repository, "confirm_reservation")
