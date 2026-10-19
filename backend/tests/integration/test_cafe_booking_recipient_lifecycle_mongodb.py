"""Real MongoDB acceptance for cafe recipient lifecycle."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient

from app.services import (
    cafe_booking_recipient_worker_lifecycle_service as lifecycle,
)
from app.services import (
    cafe_booking_recipient_exhausted_maintenance_service as maintenance,
)
from app.repositories import (
    cafe_booking_recipient_exhausted_recovery_repository as recovery,
)


def test_real_mongodb_lifecycle_recovery():
    client = MongoClient(
        "mongodb://127.0.0.1:27017/",
        serverSelectionTimeoutMS=3000,
    )

    client.admin.command("ping")

    dbname = "xuoroni_qa_lifecycle_" + uuid4().hex
    database = client[dbname]
    collection = database.cafe_booking_recipient_deliveries

    now = datetime.now(timezone.utc)
    expired = now - timedelta(minutes=10)
    future = now + timedelta(minutes=10)

    def document(state, attempts, locked_until):
        reservation_id = str(ObjectId())
        recipient_id = str(ObjectId())
        event_type = "booking_confirmed"

        return {
            "_id": (
                f"{reservation_id}:{event_type}:"
                f"customer:{recipient_id}"
            ),
            "event_key": f"{reservation_id}:{event_type}",
            "event_type": event_type,
            "recipient_type": "customer",
            "recipient_id": ObjectId(recipient_id),
            "delivery_state": state,
            "attempt_count": attempts,
            "locked_until": locked_until,
            "lease_token": "a" * 64,
            "claimed_at": expired,
            "next_attempt_at": None,
        }

    exhausted_a = document("processing", 8, expired)
    exhausted_b = document("processing", 9, expired)
    active = document("processing", 8, future)
    pending = document("pending", 8, expired)
    retryable = document("processing", 7, expired)

    try:
        collection.insert_many([
            exhausted_a,
            exhausted_b,
            active,
            pending,
            retryable,
        ])

        qa_db = SimpleNamespace(
            cafe_booking_recipient_deliveries=collection
        )

        with patch.object(maintenance.mongo, "db", qa_db):
            with patch.object(recovery.mongo, "db", qa_db):
                with patch.object(
                    lifecycle,
                    "run_bounded_cafe_booking_recipient_worker",
                ) as delivery:

                    first = lifecycle.run_cafe_booking_recipient_lifecycle(
                        enabled=True,
                        maintenance_enabled=True,
                        delivery_enabled=False,
                        max_maintenance_records=1,
                    )

                    assert first["outcome"] == "completed"
                    assert first["maintenance"]["examined_count"] == 1
                    assert first["maintenance"]["reconciled_count"] == 1
                    assert first["delivery"] is None

                    second = lifecycle.run_cafe_booking_recipient_lifecycle(
                        enabled=True,
                        maintenance_enabled=True,
                        delivery_enabled=False,
                        max_maintenance_records=10,
                    )

                    assert second["maintenance"]["reconciled_count"] == 1

                    third = lifecycle.run_cafe_booking_recipient_lifecycle(
                        enabled=True,
                        maintenance_enabled=True,
                        delivery_enabled=False,
                    )

                    assert third["maintenance"]["examined_count"] == 0
                    assert third["maintenance"]["reconciled_count"] == 0

                    delivery.assert_not_called()

        for item in (exhausted_a, exhausted_b):
            stored = collection.find_one({"_id": item["_id"]})
            assert stored["delivery_state"] == "dead_letter"
            assert stored["last_error"] == "DELIVERY_LEASE_EXHAUSTED"
            assert stored["locked_until"] is None
            assert stored["next_attempt_at"] is None
            assert "lease_token" not in stored

        for item in (active, pending, retryable):
            stored = collection.find_one({"_id": item["_id"]})
            assert stored["delivery_state"] == item["delivery_state"]
            assert stored["attempt_count"] == item["attempt_count"]
            assert stored["locked_until"] == (
                item["locked_until"]
                .replace(tzinfo=None)
                .replace(
                    microsecond=(
                        item["locked_until"].microsecond // 1000
                    ) * 1000
                )
            )

        assert collection.count_documents({}) == 5

    finally:
        client.drop_database(dbname)
        client.close()