"""Real MongoDB cafe transaction and outbox acceptance."""

import os
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.extensions import mongo
from app.repositories import (
    cafe_reservation_lifecycle_repository as lifecycle,
)


def test_booking_commit_isolation_and_outbox_rollback():
    uri = os.getenv(
        "XUORONI_QA_MONGO_URI",
        "mongodb://127.0.0.1:27017/",
    )

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    # Transaction support is required. Never silently fake it.
    hello = client.admin.command("hello")
    if not (
        hello.get("setName") or hello.get("msg") == "isdbgrid"
    ):
        client.close()
        pytest.fail(
            "Real MongoDB transactions require a replica set "
            "or sharded deployment."
        )

    name = "xuoroni_qa_booking_tx_" + uuid4().hex
    db = client[name]

    partner_id = ObjectId()
    other_partner_id = ObjectId()
    venue_id = ObjectId()
    other_venue_id = ObjectId()
    customer_id = ObjectId()
    booking_id = ObjectId()

    try:
        db.cafe_booking_outbox.create_index(
            "event_key", unique=True
        )

        db.venues.insert_many([
            {
                "_id": venue_id,
                "owner_id": str(partner_id),
                "status": "approved",
                "active": True,
                "reservation_fence_version": 0,
            },
            {
                "_id": other_venue_id,
                "owner_id": str(other_partner_id),
                "status": "approved",
                "active": True,
                "reservation_fence_version": 0,
            },
        ])

        db.cafe_partner_accounts.insert_many([
            {
                "_id": partner_id,
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "venue_id": venue_id,
            },
            {
                "_id": other_partner_id,
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "venue_id": other_venue_id,
            },
        ])

        db.reservations.insert_one({
            "_id": booking_id,
            "venue_id": venue_id,
            "customer_id": customer_id,
            "status": "pending",
        })

        def transition(actor, decision):
            with client.start_session() as session:
                return session.with_transaction(
                    lambda active: (
                        lifecycle.transition_cafe_reservation_status(
                            reservation_id=str(booking_id),
                            actor_id=str(actor),
                            actor_role="cafe_partner",
                            decision=decision,
                            session=active,
                        )
                    )
                )

        with patch.object(mongo, "db", db):
            # Cross-venue denial must precede any change.
            with pytest.raises(
                lifecycle.CafeReservationLifecycleDenied
            ):
                transition(other_partner_id, "confirmed")

            assert db.reservations.find_one(
                {"_id": booking_id}
            )["status"] == "pending"

            assert db.cafe_booking_outbox.count_documents({}) == 0

            # Failure after the reservation CAS must roll back
            # the reservation and the venue write fence.
            with patch.object(
                lifecycle,
                "record_cafe_booking_event",
                side_effect=RuntimeError("QA_OUTBOX_FAILURE"),
            ):
                with pytest.raises(
                    RuntimeError, match="QA_OUTBOX_FAILURE"
                ):
                    transition(partner_id, "confirmed")

            assert db.reservations.find_one(
                {"_id": booking_id}
            )["status"] == "pending"

            assert db.cafe_booking_outbox.count_documents({}) == 0

            assert db.venues.find_one(
                {"_id": venue_id}
            )["reservation_fence_version"] == 0

            # Successful transaction must commit both records.
            result = transition(partner_id, "confirmed")

        assert result["status"] == "confirmed"
        assert result["reservation_id"] == str(booking_id)
        assert result["venue_id"] == str(venue_id)

        stored = db.reservations.find_one({"_id": booking_id})
        assert stored["status"] == "confirmed"

        event = db.cafe_booking_outbox.find_one({
            "event_key": f"{booking_id}:booking_confirmed"
        })

        assert event is not None
        assert event["reservation_id"] == booking_id
        assert event["venue_id"] == venue_id
        assert event["customer_id"] == customer_id
        assert event["delivery_state"] == "pending"

        assert db.cafe_booking_outbox.count_documents({}) == 1
        assert db.venues.find_one(
            {"_id": venue_id}
        )["reservation_fence_version"] == 1

    finally:
        client.drop_database(name)
        client.close()