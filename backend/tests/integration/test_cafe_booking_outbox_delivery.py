"""Permanent mocked cafe booking outbox delivery safety contracts.

These tests do not establish real MongoDB concurrency or provider
delivery guarantees. They verify repository query and CAS contracts.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.repositories import (
    cafe_booking_outbox_delivery_repository as lease_repo,
    cafe_booking_outbox_receipt_repository as receipt_repo,
)


NOW = datetime(2026, 10, 17, 13, tzinfo=timezone.utc)
EVENT = str(ObjectId())
RESERVATION = ObjectId()
VENUE = ObjectId()
CUSTOMER = ObjectId()
TOKEN = "a" * 64


@pytest.fixture
def database(monkeypatch):
    collection = Mock()
    mongo = SimpleNamespace(
        db=SimpleNamespace(cafe_booking_outbox=collection)
    )
    monkeypatch.setattr(lease_repo, "mongo", mongo)
    monkeypatch.setattr(receipt_repo, "mongo", mongo)
    return collection


def claim(database, **overrides):
    def result(*args, **kwargs):
        update = args[1]
        return {
            "_id": ObjectId(EVENT),
            "event_key": str(RESERVATION) + ":booking_created",
            "reservation_id": RESERVATION,
            "venue_id": VENUE,
            "customer_id": CUSTOMER,
            "event_type": "booking_created",
            "delivery_state": "processing",
            "lease_token": update["$set"]["lease_token"],
            "locked_until": update["$set"]["locked_until"],
            "attempt_count": 1,
        }

    database.find_one_and_update.side_effect = result
    values = {"now": NOW, "lease_seconds": 90}
    values.update(overrides)
    return lease_repo.claim_next_cafe_booking_event(**values)


def acknowledge(database, **overrides):
    database.update_one.return_value = SimpleNamespace(
        matched_count=1, modified_count=1
    )
    values = {
        "event_id": EVENT,
        "lease_token": TOKEN,
        "attempt_count": 1,
        "now": NOW,
    }
    values.update(overrides)
    return receipt_repo.acknowledge_cafe_booking_delivery(**values)


def fail(database, attempt=1, **overrides):
    database.update_one.return_value = SimpleNamespace(
        matched_count=1, modified_count=1
    )
    values = {
        "event_id": EVENT,
        "lease_token": TOKEN,
        "attempt_count": attempt,
        "failure_code": "PROVIDER_UNAVAILABLE",
        "now": NOW,
    }
    values.update(overrides)
    return receipt_repo.record_cafe_booking_delivery_failure(**values)


def test_atomic_claim_matches_due_and_expired_states(database):
    result = claim(database)

    assert result["event_id"] == EVENT
    assert result["attempt_count"] == 1
    assert len(result["lease_token"]) == 64

    args = database.find_one_and_update.call_args
    query = args.args[0]
    update = args.args[1]

    assert len(query["$or"]) == 2
    assert query["$or"][0]["delivery_state"] == "pending"
    assert query["$or"][0]["next_attempt_at"] == {"$lte": NOW}
    assert query["$or"][1]["delivery_state"] == "processing"
    assert query["$or"][1]["locked_until"] == {"$lte": NOW}
    assert update["$inc"] == {"attempt_count": 1}
    assert update["$set"]["delivery_state"] == "processing"


def test_claim_uses_distinct_tokens(database):
    first = claim(database)
    second = claim(database)

    assert first["lease_token"] != second["lease_token"]


def test_empty_queue_returns_none(database):
    database.find_one_and_update.return_value = None

    assert lease_repo.claim_next_cafe_booking_event(
        now=NOW
    ) is None


@pytest.mark.parametrize(
    "duration", [0, 29, 901, True, "90", 1.5]
)
def test_invalid_lease_duration_rejected(database, duration):
    with pytest.raises(
        lease_repo.CafeBookingDeliveryLeaseError
    ):
        claim(database, lease_seconds=duration)

    database.find_one_and_update.assert_not_called()


def test_invalid_claim_acknowledgement_rejected(database):
    database.find_one_and_update.return_value = {
        "_id": ObjectId(EVENT),
        "delivery_state": "processing",
        "lease_token": "invalid",
    }

    with pytest.raises(
        lease_repo.CafeBookingDeliveryLeaseError
    ):
        lease_repo.claim_next_cafe_booking_event(now=NOW)


def test_delivery_acknowledgement_requires_current_lease(database):
    result = acknowledge(database)

    assert result["delivery_state"] == "delivered"

    query, update = database.update_one.call_args.args

    assert query["_id"] == ObjectId(EVENT)
    assert query["lease_token"] == TOKEN
    assert query["attempt_count"] == 1
    assert query["delivery_state"] == "processing"
    assert query["locked_until"] == {"$gt": NOW}

    assert update["$set"]["delivery_state"] == "delivered"
    assert update["$set"]["locked_until"] is None
    assert update["$set"]["delivered_at"] == NOW
    assert "lease_token" in update["$unset"]


@pytest.mark.parametrize(
    "attempt", [1, 2, 3, 4, 5, 6, 7]
)
def test_failure_schedules_exponential_retry(database, attempt):
    result = fail(database, attempt=attempt)

    expected = NOW + timedelta(
        seconds=min(30 * 2 ** (attempt - 1), 3600)
    )

    assert result["delivery_state"] == "pending"
    assert result["next_attempt_at"] == expected

    query, update = database.update_one.call_args.args

    assert query["lease_token"] == TOKEN
    assert query["attempt_count"] == attempt
    assert update["$set"]["next_attempt_at"] == expected
    assert update["$set"]["locked_until"] is None
    assert "lease_token" in update["$unset"]


@pytest.mark.parametrize("attempt", [8, 9])
def test_retry_limit_moves_to_dead_letter(database, attempt):
    result = fail(database, attempt=attempt)

    assert result["delivery_state"] == "dead_letter"
    assert result["next_attempt_at"] is None

    update = database.update_one.call_args.args[1]
    assert update["$set"]["dead_lettered_at"] == NOW
    assert update["$set"]["last_error"] == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    "invalid", ["", "free text with personal data", "x" * 100]
)
def test_unstructured_failure_codes_rejected(database, invalid):
    with pytest.raises(receipt_repo.CafeBookingReceiptError):
        fail(database, failure_code=invalid)

    database.update_one.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_id": "invalid"},
        {"lease_token": "wrong"},
        {"attempt_count": 0},
        {"attempt_count": True},
        {"now": datetime(2026, 10, 17)},
    ],
)
def test_invalid_receipt_inputs_fail_before_write(
    database, overrides
):
    with pytest.raises(receipt_repo.CafeBookingReceiptError):
        acknowledge(database, **overrides)

    database.update_one.assert_not_called()


@pytest.mark.parametrize("action", ["delivered", "failed"])
def test_stale_lease_cannot_modify_delivery(database, action):
    database.update_one.return_value = SimpleNamespace(
        matched_count=0, modified_count=0
    )

    values = dict(
        event_id=EVENT,
        lease_token=TOKEN,
        attempt_count=1,
        now=NOW,
    )

    if action == "delivered":
        function = receipt_repo.acknowledge_cafe_booking_delivery
    else:
        function = receipt_repo.record_cafe_booking_delivery_failure
        values["failure_code"] = "PROVIDER_UNAVAILABLE"

    with pytest.raises(receipt_repo.CafeBookingReceiptConflict):
        function(**values)

    database.update_one.assert_called_once()


def test_database_failure_propagates_from_claim(database):
    database.find_one_and_update.side_effect = OperationFailure(
        "database unavailable"
    )

    with pytest.raises(OperationFailure):
        lease_repo.claim_next_cafe_booking_event(now=NOW)


def test_database_failure_propagates_from_receipt(database):
    database.update_one.side_effect = OperationFailure(
        "database unavailable"
    )

    with pytest.raises(OperationFailure):
        acknowledge(database)
