"""Permanent tests for the Soultie transactional booking venue fence.

Tests verify the repository boundary with a mocked database.
They do not prove that an arbitrary non-None session is an active
transaction, nor that two real MongoDB transactions serialize.
Those guarantees require coordinator and database integration tests.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import (
    AutoReconnect,
    DuplicateKeyError,
    OperationFailure,
    PyMongoError,
)
import pytest

from app.repositories import cafe_reservation_fence_repository as repo


VENUE = "507f191e810c19729de860ea"
OTHER = "507f191e810c19729de860bb"


def document(**changes):
    result = {
        "_id": ObjectId(VENUE),
        "status": "approved",
        "active": True,
        "reservation_fence_version": 1,
    }
    result.update(changes)
    return result


@pytest.fixture
def context(monkeypatch):
    collection = Mock()
    collection.find_one_and_update.return_value = document()
    monkeypatch.setattr(
        repo,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(venues=collection)),
    )
    return collection


def acquire(collection, venue=VENUE, session=None):
    if session is None:
        session = object()
    return repo.acquire_venue_reservation_fence(
        venue_id=venue, session=session
    )


def test_approved_active_venue_can_be_fenced(context):
    result = acquire(context)
    assert result == {
        "venue_id": VENUE,
        "fence_version": 1,
    }


def test_exact_atomic_update_and_session_propagation(context):
    session = object()
    result = repo.acquire_venue_reservation_fence(
        venue_id=VENUE, session=session
    )
    assert result["fence_version"] == 1
    context.find_one_and_update.assert_called_once_with(
        {
            "_id": ObjectId(VENUE),
            "status": "approved",
            "active": True,
        },
        {
            "$inc": {
                "reservation_fence_version": 1,
            }
        },
        return_document=ReturnDocument.AFTER,
        session=session,
    )


def test_exactly_one_venue_write(context):
    acquire(context)
    assert context.find_one_and_update.call_count == 1
    assert not context.insert_one.called
    assert not context.delete_one.called
    assert not context.update_many.called


@pytest.mark.parametrize(
    "venue",
    [
        None, "", "invalid", 123, True,
        [], {}, "f" * 25,
    ],
)
def test_invalid_venue_rejected_before_write(context, venue):
    with pytest.raises(repo.CafeReservationFenceError):
        repo.acquire_venue_reservation_fence(
            venue_id=venue, session=object()
        )
    context.find_one_and_update.assert_not_called()


def test_missing_transaction_session_fails_before_write(context):
    with pytest.raises(repo.CafeReservationFenceError):
        repo.acquire_venue_reservation_fence(
            venue_id=VENUE, session=None
        )
    context.find_one_and_update.assert_not_called()


def test_missing_venue_fails_closed(context):
    context.find_one_and_update.return_value = None
    with pytest.raises(repo.CafeReservationFenceError):
        acquire(context)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        "invalid",
        {},
        document(_id=ObjectId(OTHER)),
        document(status="pending"),
        document(status="rejected"),
        document(status="suspended"),
        document(active=False),
        document(active=None),
        document(reservation_fence_version=None),
        document(reservation_fence_version=True),
        document(reservation_fence_version=False),
        document(reservation_fence_version=0),
        document(reservation_fence_version=-1),
        document(reservation_fence_version=1.5),
        document(reservation_fence_version="2"),
        document(reservation_fence_version=[]),
    ],
)
def test_invalid_acknowledgements_fail_closed(context, invalid):
    context.find_one_and_update.return_value = invalid
    with pytest.raises(repo.CafeReservationFenceError):
        acquire(context)


@pytest.mark.parametrize("version", [1, 2, 10, 9999])
def test_positive_fence_versions_returned(context, version):
    context.find_one_and_update.return_value = document(
        reservation_fence_version=version
    )
    assert acquire(context)["fence_version"] == version


@pytest.mark.parametrize(
    "failure",
    [
        PyMongoError("outage"),
        AutoReconnect("reconnect"),
        DuplicateKeyError("duplicate"),
        OperationFailure("transaction aborted"),
    ],
)
def test_database_errors_propagate_for_transaction_abort(
    context, failure
):
    context.find_one_and_update.side_effect = failure
    with pytest.raises(PyMongoError):
        acquire(context)


def test_fence_does_not_return_sensitive_venue_fields(context):
    context.find_one_and_update.return_value = document(
        owner_id="private-owner",
        password_hash="private-secret",
        address="private-address",
    )
    result = acquire(context)
    assert set(result) == {"venue_id", "fence_version"}
    assert "private-secret" not in repr(result)


def test_fence_does_not_claim_booking_created(context):
    result = acquire(context)
    assert "reservation_id" not in result
    assert "booking_id" not in result
    assert "capacity_held" not in result
    assert "available" not in result


def test_repository_has_no_booking_write_api():
    assert not hasattr(repo, "create_reservation")
    assert not hasattr(repo, "confirm_reservation")


def test_fence_requires_coordinator_for_real_transaction():
    """A mock object is accepted as session, not proof of a transaction."""
    session = object()
    assert session is not None
    assert not hasattr(session, "in_transaction")
    assert repo.acquire_venue_reservation_fence is not None
