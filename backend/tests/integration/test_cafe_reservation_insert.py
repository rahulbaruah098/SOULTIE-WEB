"""Permanent transactional reservation insert security contracts."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError
import pytest

from app.repositories import reservation_repository as repo
from app.schemas.reservation import ReservationValidationError


VENUE = "507f191e810c19729de860ea"
CUSTOMER = "507f1f77bcf86cd799439011"
START = datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 30, 9, 0, tzinfo=timezone.utc)


class FakeSession:
    in_transaction = True


def request():
    return {
        "venue_id": VENUE,
        "start_at": "2026-12-01T19:00:00+05:30",
        "party_size": 4,
        "idempotency_key": "booking-request-000001",
        "notes": " Window seat ",
    }


@pytest.fixture
def context(monkeypatch):
    collection = Mock()
    inserted = ObjectId()
    collection.insert_one.return_value = SimpleNamespace(
        inserted_id=inserted
    )
    monkeypatch.setattr(
        repo,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(reservations=collection)
        ),
    )
    return SimpleNamespace(
        collection=collection,
        inserted=inserted,
        session=FakeSession(),
    )


def insert(context, **overrides):
    arguments = {
        "customer_id": CUSTOMER,
        "request_data": request(),
        "duration_minutes": 60,
        "buffer_minutes": 15,
        "session": context.session,
        "now": NOW,
    }
    arguments.update(overrides)
    return repo.insert_pending_cafe_reservation(**arguments)


def test_pending_reservation_insert(context):
    result = insert(context)
    assert result == {
        "reservation_id": str(context.inserted),
        "venue_id": VENUE,
        "status": "pending",
        "start_at": START,
        "end_at": START + timedelta(minutes=75),
        "party_size": 4,
    }
    context.collection.insert_one.assert_called_once()


def test_document_fields_and_session(context):
    insert(context)
    args, kwargs = context.collection.insert_one.call_args
    assert kwargs == {"session": context.session}
    doc = args[0]
    assert doc["venue_id"] == ObjectId(VENUE)
    assert doc["customer_id"] == ObjectId(CUSTOMER)
    assert doc["start_at"] == START
    assert doc["end_at"] == START + timedelta(minutes=75)
    assert doc["party_size"] == 4
    assert doc["duration_minutes"] == 60
    assert doc["buffer_minutes"] == 15
    assert doc["status"] == "pending"
    assert doc["notes"] == "Window seat"
    assert doc["created_at"] == NOW
    assert doc["updated_at"] == NOW
    assert doc["idempotency_key"] == "booking-request-000001"
    assert "owner_id" not in doc
    assert "approved_at" not in doc


def test_caller_request_is_not_mutated(context):
    data = request()
    previous = deepcopy(data)
    insert(context, request_data=data)
    assert data == previous


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
def test_inactive_sessions_fail_before_insert(context, session):
    with pytest.raises(repo.ReservationRepositoryError):
        insert(context, session=session)
    context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "customer_id",
    [None, "", "invalid", 123, True, [], {}, "f" * 25],
)
def test_invalid_customer_id_fails_closed(context, customer_id):
    with pytest.raises(repo.ReservationRepositoryError):
        insert(context, customer_id=customer_id)
    context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "duration",
    [None, True, False, 0, 14, 721, "60", 1.5],
)
def test_invalid_duration_fails_closed(context, duration):
    with pytest.raises(repo.ReservationRepositoryError):
        insert(context, duration_minutes=duration)
    context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "buffer",
    [None, True, False, -1, 181, "15", 1.5],
)
def test_invalid_buffer_fails_closed(context, buffer):
    with pytest.raises(repo.ReservationRepositoryError):
        insert(context, buffer_minutes=buffer)
    context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "now",
    ["bad", 123, datetime(2026, 11, 30), None],
)
def test_timestamp_handling(context, now):
    if now is None:
        result = insert(context, now=None)
        assert result["status"] == "pending"
    else:
        with pytest.raises(repo.ReservationRepositoryError):
            insert(context, now=now)
        context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("venue_id", "invalid"),
        ("party_size", True),
        ("party_size", 101),
        ("idempotency_key", "short"),
        ("start_at", "2026-12-01T19:00:00"),
        ("status", "approved"),
        ("owner_id", CUSTOMER),
    ],
)
def test_invalid_request_does_not_insert(context, field, value):
    data = request()
    data[field] = value
    with pytest.raises(ReservationValidationError):
        insert(context, request_data=data)
    context.collection.insert_one.assert_not_called()


@pytest.mark.parametrize(
    "ack",
    [None, "invalid", 123, ObjectId, "some-id"],
)
def test_invalid_insert_acknowledgements_fail_closed(context, ack):
    context.collection.insert_one.return_value = (
        None if ack is None
        else SimpleNamespace(inserted_id=ack)
    )
    with pytest.raises(repo.ReservationRepositoryError):
        insert(context)


def test_duplicate_idempotency_error_propagates(context):
    context.collection.insert_one.side_effect = DuplicateKeyError(
        "duplicate idempotency key"
    )
    with pytest.raises(DuplicateKeyError):
        insert(context)


def test_database_error_propagates(context):
    context.collection.insert_one.side_effect = PyMongoError(
        "simulated outage"
    )
    with pytest.raises(PyMongoError):
        insert(context)


def test_insert_does_not_confirm_booking(context):
    result = insert(context)
    assert result["status"] == "pending"
    assert "confirmed_at" not in result
    assert "payment_status" not in result
    context.collection.update_one.assert_not_called()
    context.collection.delete_one.assert_not_called()
    context.collection.find_one_and_update.assert_not_called()


def test_function_not_exposed_as_http_route(context):
    assert callable(repo.insert_pending_cafe_reservation)
    assert "reservation_id" in insert(context)
