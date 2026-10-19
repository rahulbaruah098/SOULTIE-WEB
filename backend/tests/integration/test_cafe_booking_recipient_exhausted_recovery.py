"""Atomic exhausted-recipient reconciliation contracts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bson import ObjectId

from app.repositories import (
    cafe_booking_recipient_exhausted_recovery_repository as recovery,
)


NOW = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


def make_key():
    return (
        str(ObjectId()) +
        ":booking_confirmed:customer:" +
        str(ObjectId())
    )


class FakeCollection:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def find_one_and_update(self, query, update, **kwargs):
        self.calls.append((query, update, kwargs))
        return self.result


def test_atomic_query_requires_expired_exhausted_processing_lease():
    key = make_key()
    collection = FakeCollection()

    with patch.object(
        recovery.mongo, "db",
        SimpleNamespace(cafe_booking_recipient_deliveries=collection),
    ):
        assert recovery.reconcile_exhausted_cafe_booking_recipient(
            delivery_key=key, now=NOW
        ) is None

    assert len(collection.calls) == 1
    query, update, _ = collection.calls[0]

    assert query["_id"] == key
    assert query["delivery_state"] == "processing"
    assert query["attempt_count"] == {"$gte": 8}
    assert query["locked_until"] == {
        "$type": "date",
        "$lte": NOW,
    }

    assert update["$set"]["delivery_state"] == "dead_letter"
    assert update["$set"]["next_attempt_at"] is None
    assert update["$set"]["last_error"] == "DELIVERY_LEASE_EXHAUSTED"
    assert "lease_token" in update["$unset"]


@pytest.mark.parametrize(
    "invalid", [None, "bad", 0, datetime(2026, 10, 18)]
)
def test_invalid_time_never_writes(invalid):
    collection = FakeCollection()

    with patch.object(
        recovery.mongo, "db",
        SimpleNamespace(cafe_booking_recipient_deliveries=collection),
    ):
        with pytest.raises(recovery.CafeRecipientExhaustedRecoveryError):
            recovery.reconcile_exhausted_cafe_booking_recipient(
                delivery_key=make_key(), now=invalid
            ) if invalid is not None else (
                recovery.reconcile_exhausted_cafe_booking_recipient(
                    delivery_key=make_key(), now="bad"
                )
            )

    assert collection.calls == []


def test_successful_reconciliation_returns_safe_summary():
    key = make_key()
    collection = FakeCollection({
        "_id": key,
        "delivery_state": "dead_letter",
        "attempt_count": 8,
        "next_attempt_at": None,
    })

    with patch.object(
        recovery.mongo, "db",
        SimpleNamespace(cafe_booking_recipient_deliveries=collection),
    ):
        result = recovery.reconcile_exhausted_cafe_booking_recipient(
            delivery_key=key, now=NOW
        )

    assert result == {
        "delivery_key": key,
        "delivery_state": "dead_letter",
        "attempt_count": 8,
        "recovery_reason": "DELIVERY_LEASE_EXHAUSTED",
    }


def test_invalid_database_acknowledgement_fails_closed():
    key = make_key()
    collection = FakeCollection({
        "_id": key,
        "delivery_state": "processing",
        "attempt_count": 8,
    })

    with patch.object(
        recovery.mongo, "db",
        SimpleNamespace(cafe_booking_recipient_deliveries=collection),
    ):
        with pytest.raises(recovery.CafeRecipientExhaustedRecoveryError):
            recovery.reconcile_exhausted_cafe_booking_recipient(
                delivery_key=key, now=NOW
            )
