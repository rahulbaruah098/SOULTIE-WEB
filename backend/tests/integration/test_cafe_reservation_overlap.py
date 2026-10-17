"""Transactional overlapping reservation read security contracts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import AutoReconnect, PyMongoError
import pytest

from app.repositories import reservation_repository as repo


VENUE = "507f191e810c19729de860ea"
OTHER = "507f191e810c19729de860bb"
START = datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc)
END = START + timedelta(minutes=75)


class Session:
    in_transaction = True


def booking(
    *,
    venue=VENUE,
    offset=0,
    duration=60,
    buffer=15,
    party=4,
    status="confirmed",
):
    beginning = START + timedelta(minutes=offset)
    return {
        "_id": ObjectId(),
        "venue_id": ObjectId(venue),
        "status": status,
        "start_at": beginning,
        "end_at": beginning + timedelta(
            minutes=duration + buffer
        ),
        "party_size": party,
        "duration_minutes": duration,
        "buffer_minutes": buffer,
    }


@pytest.fixture
def context(monkeypatch):
    cursor = Mock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = []

    collection = Mock()
    collection.find.return_value = cursor

    monkeypatch.setattr(
        repo,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(reservations=collection)
        ),
    )

    return SimpleNamespace(
        collection=collection,
        cursor=cursor,
        session=Session(),
    )


def lookup(context, **changes):
    arguments = {
        "venue_id": VENUE,
        "start_at": START,
        "end_at": END,
        "session": context.session,
    }
    arguments.update(changes)
    return repo.find_overlapping_active_reservations(**arguments)


def set_rows(context, rows):
    context.cursor.limit.return_value = rows


def test_empty_snapshot(context):
    assert lookup(context) == []
    context.collection.find.assert_called_once()
    context.cursor.limit.assert_called_once_with(501)


def test_exact_query_and_transaction_session(context):
    lookup(context)

    context.collection.find.assert_called_once_with(
        {
            "venue_id": ObjectId(VENUE),
            "status": {"$in": ["confirmed", "pending"]},
            "start_at": {"$lt": END},
            "$or": [
                {"end_at": {"$gt": START}},
                {"end_at": {"$exists": False}},
                {"end_at": None},
            ],
        },
        session=context.session,
    )

    context.cursor.sort.assert_called_once_with([
        ("start_at", 1), ("_id", 1)
    ])


@pytest.mark.parametrize(
    "offset",
    [-74, -60, -30, 0, 30, 74],
)
def test_overlapping_intervals_accepted(context, offset):
    row = booking(offset=offset)
    set_rows(context, [row])
    assert lookup(context) == [row]


def test_pending_reservation_included(context):
    row = booking(status="pending")
    set_rows(context, [row])
    assert lookup(context) == [row]


def test_multiple_valid_reservations(context):
    rows = [
        booking(offset=-30, status="pending"),
        booking(offset=0, status="confirmed"),
        booking(offset=30, status="confirmed"),
    ]
    set_rows(context, rows)
    assert lookup(context) == rows


def test_end_equal_to_request_start_excluded_by_query(context):
    lookup(context)
    query = context.collection.find.call_args.args[0]
    assert query["$or"][0] == {
        "end_at": {"$gt": START}
    }


def test_start_equal_to_request_end_excluded_by_query(context):
    lookup(context)
    query = context.collection.find.call_args.args[0]
    assert query["start_at"] == {"$lt": END}


@pytest.mark.parametrize(
    "venue",
    [
        None, "", "bad", 123, True,
        [], {}, "f" * 25,
    ],
)
def test_invalid_venue_rejected_without_database_read(
    context, venue
):
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(context, venue_id=venue)
    context.collection.find.assert_not_called()


@pytest.mark.parametrize(
    "session",
    [
        None,
        object(),
        SimpleNamespace(in_transaction=False),
        SimpleNamespace(in_transaction=None),
        SimpleNamespace(in_transaction=1),
        SimpleNamespace(in_transaction="True"),
    ],
)
def test_missing_or_invalid_session_rejected(context, session):
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(context, session=session)
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
    ],
)
def test_invalid_windows_rejected(context, start, end):
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(context, start_at=start, end_at=end)
    context.collection.find.assert_not_called()


def test_large_window_rejected(context):
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(
            context,
            end_at=START + timedelta(days=367),
        )
    context.collection.find.assert_not_called()


def test_timezone_range_normalized(context):
    offset = timezone(timedelta(hours=5, minutes=30))
    lookup(
        context,
        start_at=START.astimezone(offset),
        end_at=END.astimezone(offset),
    )
    query = context.collection.find.call_args.args[0]
    assert query["start_at"] == {"$lt": END}
    assert query["$or"][0] == {
        "end_at": {"$gt": START}
    }


@pytest.mark.parametrize(
    "invalid_row",
    [
        None,
        [],
        "bad",
        {},
        booking(venue=OTHER),
        booking(status="cancelled"),
        booking(status="rejected"),
        booking(status="completed"),
        booking(offset=-75),
        booking(offset=75),
        {**booking(), "end_at": None},
        {**booking(), "end_at": "invalid"},
        {**booking(), "start_at": None},
        {**booking(), "end_at": END + timedelta(days=2)},
        {**booking(), "party_size": True},
        {**booking(), "party_size": 0},
        {**booking(), "duration_minutes": 0},
        {**booking(), "buffer_minutes": -1},
        {**booking(), "buffer_minutes": None},
    ],
)
def test_invalid_database_rows_fail_closed(
    context, invalid_row
):
    set_rows(context, [invalid_row])
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(context)


def test_result_limit_boundary(context):
    rows = [booking() for _ in range(500)]
    set_rows(context, rows)
    assert len(lookup(context)) == 500


def test_result_overflow_rejected(context):
    set_rows(context, [booking() for _ in range(501)])
    with pytest.raises(repo.ReservationRepositoryError):
        lookup(context)


@pytest.mark.parametrize(
    "failure",
    [
        PyMongoError("outage"),
        AutoReconnect("reconnect"),
    ],
)
def test_database_failure_propagates(context, failure):
    context.collection.find.side_effect = failure
    with pytest.raises(PyMongoError):
        lookup(context)


def test_read_does_not_mutate_reservation(context):
    lookup(context)
    context.collection.insert_one.assert_not_called()
    context.collection.update_one.assert_not_called()
    context.collection.delete_one.assert_not_called()
    context.collection.find_one_and_update.assert_not_called()


def test_empty_result_does_not_claim_capacity(context):
    assert lookup(context) == []
    assert not hasattr(
        repo, "approve_booking_capacity"
    )
