"""Permanent bounded cafe exhausted-recipient maintenance contracts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bson import ObjectId

from app.services import (
    cafe_booking_recipient_exhausted_maintenance_service as maintenance,
)


NOW = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)


def make_key():
    return f"{ObjectId()}:booking_confirmed:customer:{ObjectId()}"


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, value):
        assert value == [("locked_until", 1), ("_id", 1)]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


class Collection:
    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    def find(self, query, projection):
        assert projection == {"_id": 1}
        self.queries.append(query)
        return Cursor(list(self.documents))


def database(collection):
    return SimpleNamespace(
        cafe_booking_recipient_deliveries=collection
    )


def test_disabled_worker_never_reads_database():
    collection = Collection([{"_id": make_key()}])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with patch.object(
            maintenance, "reconcile_exhausted_cafe_booking_recipient"
        ) as reconcile:
            for enabled in (False, None, 0, 1, "true"):
                result = (
                    maintenance.maintain_exhausted_cafe_booking_recipients(
                        enabled=enabled,
                        now=NOW,
                    )
                )
                assert result == {
                    "outcome": "disabled",
                    "examined_count": 0,
                    "reconciled_count": 0,
                }

            reconcile.assert_not_called()

    assert collection.queries == []


@pytest.mark.parametrize(
    "invalid",
    [0, -1, 101, True, False, 1.5, "10", None],
)
def test_invalid_batch_limit_fails_before_query(invalid):
    collection = Collection([])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with pytest.raises(maintenance.CafeRecipientMaintenanceError):
            maintenance.maintain_exhausted_cafe_booking_recipients(
                enabled=True,
                max_records=invalid,
                now=NOW,
            )

    assert collection.queries == []


@pytest.mark.parametrize(
    "invalid", ["invalid", 10, datetime(2026, 10, 18)]
)
def test_invalid_timestamp_fails_before_query(invalid):
    collection = Collection([])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with pytest.raises(maintenance.CafeRecipientMaintenanceError):
            maintenance.maintain_exhausted_cafe_booking_recipients(
                enabled=True,
                now=invalid,
            )

    assert collection.queries == []


def test_bounded_recovery_and_concurrent_loss():
    keys = [make_key() for _ in range(6)]
    collection = Collection([{"_id": key} for key in keys])

    def reconcile(*, delivery_key, now):
        assert now == NOW
        if delivery_key == keys[1]:
            # Another maintenance worker won the atomic race.
            return None
        return {
            "delivery_key": delivery_key,
            "delivery_state": "dead_letter",
            "attempt_count": 8,
            "recovery_reason": "DELIVERY_LEASE_EXHAUSTED",
        }

    with patch.object(maintenance.mongo, "db", database(collection)):
        with patch.object(
            maintenance,
            "reconcile_exhausted_cafe_booking_recipient",
            side_effect=reconcile,
        ) as operation:
            result = (
                maintenance.maintain_exhausted_cafe_booking_recipients(
                    enabled=True,
                    max_records=3,
                    now=NOW,
                )
            )

    assert result == {
        "outcome": "completed",
        "examined_count": 3,
        "reconciled_count": 2,
    }
    assert operation.call_count == 3
    assert len(collection.queries) == 1
    assert collection.queries[0] == {
        "delivery_state": "processing",
        "attempt_count": {"$gte": 8},
        "locked_until": {"$type": "date", "$lte": NOW},
    }


def test_empty_maintenance_scan():
    collection = Collection([])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with patch.object(
            maintenance, "reconcile_exhausted_cafe_booking_recipient"
        ) as operation:
            result = (
                maintenance.maintain_exhausted_cafe_booking_recipients(
                    enabled=True,
                    max_records=10,
                    now=NOW,
                )
            )
            operation.assert_not_called()

    assert result == {
        "outcome": "completed",
        "examined_count": 0,
        "reconciled_count": 0,
    }


def test_invalid_atomic_reconciliation_result_fails_closed():
    key = make_key()
    collection = Collection([{"_id": key}])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with patch.object(
            maintenance,
            "reconcile_exhausted_cafe_booking_recipient",
            return_value={
                "delivery_key": key,
                "delivery_state": "delivered",
                "recovery_reason": "DELIVERY_LEASE_EXHAUSTED",
            },
        ):
            with pytest.raises(
                maintenance.CafeRecipientMaintenanceError
            ):
                maintenance.maintain_exhausted_cafe_booking_recipients(
                    enabled=True,
                    now=NOW,
                )


def test_invalid_database_identity_fails_closed():
    collection = Collection([{"_id": ObjectId()}])

    with patch.object(maintenance.mongo, "db", database(collection)):
        with patch.object(
            maintenance, "reconcile_exhausted_cafe_booking_recipient"
        ) as operation:
            with pytest.raises(
                maintenance.CafeRecipientMaintenanceError
            ):
                maintenance.maintain_exhausted_cafe_booking_recipients(
                    enabled=True,
                    now=NOW,
                )
            operation.assert_not_called()
