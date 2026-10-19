"""End-to-end cafe booking HTTP and MongoDB integration.

Uses:
- real Flask application
- real cafe session authentication
- real partner and venue authorization
- real reservation lifecycle
- real MongoDB transactions and outbox

Never contacts notification providers.
Never uses the application's production database.
"""

import os
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient

from app import create_app
from app.extensions import mongo
from app.services.cafe_partner_session_service import (
    create_cafe_session,
    revoke_cafe_session,
)


def test_full_cafe_booking_workflow():
    uri = os.getenv(
        "XUORONI_QA_MONGO_URI",
        "mongodb://127.0.0.1:27017/",
    )

    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=5000,
    )

    database_name = "xuoroni_qa_j22_" + uuid4().hex

    try:
        client.admin.command("ping")
        topology = client.admin.command("hello")

        assert (
            topology.get("setName")
            or topology.get("msg") == "isdbgrid"
        ), "MongoDB transactions require a replica set or mongos."

        db = client[database_name]

        owner = ObjectId()
        stranger = ObjectId()
        venue_id = ObjectId()
        other_venue = ObjectId()
        reservation_id = ObjectId()
        customer_id = ObjectId()

        now = datetime.now(timezone.utc)

        db.venues.insert_many([
            {
                "_id": venue_id,
                "owner_id": str(owner),
                "status": "approved",
                "active": True,
                "reservation_fence_version": 0,
            },
            {
                "_id": other_venue,
                "owner_id": str(stranger),
                "status": "approved",
                "active": True,
                "reservation_fence_version": 0,
            },
        ])

        db.cafe_partner_accounts.insert_many([
            {
                "_id": owner,
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "email": "qa-owner@example.invalid",
                "venue_id": venue_id,
            },
            {
                "_id": stranger,
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "email": "qa-other@example.invalid",
                "venue_id": other_venue,
            },
        ])

        db.reservations.insert_one({
            "_id": reservation_id,
            "venue_id": venue_id,
            "customer_id": customer_id,
            "status": "pending",
            "party_size": 2,
            "start_at": now + timedelta(days=2),
            "created_at": now,
            "updated_at": now,
        })

        db.cafe_booking_outbox.create_index(
            "event_key",
            unique=True,
        )

        app = create_app({"TESTING": True})

        with ExitStack() as stack:
            stack.enter_context(patch.object(mongo, "db", db))
            stack.enter_context(patch.object(mongo, "cx", client))

            own_session = create_cafe_session(
                partner_id=str(owner)
            )
            other_session = create_cafe_session(
                partner_id=str(stranger)
            )

            own_token = own_session["access_token"]
            other_token = other_session["access_token"]

            def auth(token):
                return {
                    "Authorization": f"Bearer {token}"
                }

            http = app.test_client()

            path = (
                "/api/v1/cafe-partners/bookings/"
                f"{reservation_id}/decision"
            )

            # 1. Booking list must be session-isolated.
            response = http.get(
                "/api/v1/cafe-partners/bookings?limit=20",
                headers=auth(own_token),
            )

            assert response.status_code == 200, (
                "Owner booking list: "
                f"{response.status_code} {response.json}"
            )

            payload = response.get_json()
            assert payload["venue_id"] == str(venue_id)
            assert payload["count"] == 1

            booking = payload["bookings"][0]

            assert booking["reservation_id"] == str(
                reservation_id
            )
            assert booking["status"] == "pending"

            print("OWNER BOOKING LIST: PASS")

            # 2. A second cafe must not see or edit it.
            response = http.get(
                "/api/v1/cafe-partners/bookings?limit=20",
                headers=auth(other_token),
            )

            assert response.status_code == 200
            assert response.json["bookings"] == []
            assert response.json["venue_id"] == str(
                other_venue
            )

            denied = http.post(
                path,
                headers=auth(other_token),
                json={"decision": "confirmed"},
            )

            assert denied.status_code in (403, 404)
            assert db.reservations.find_one({
                "_id": reservation_id
            })["status"] == "pending"

            assert db.cafe_booking_outbox.count_documents(
                {}
            ) == 0

            print("CROSS-VENUE ISOLATION: PASS")

            # 3. Owner confirms the booking over real HTTP.
            confirmed = http.post(
                path,
                headers=auth(own_token),
                json={"decision": "confirmed"},
            )

            assert confirmed.status_code == 200, (
                "Booking confirmation: "
                f"{confirmed.status_code} {confirmed.json}"
            )

            result = confirmed.get_json()

            assert result["reservation_id"] == str(
                reservation_id
            )
            assert result["venue_id"] == str(venue_id)
            assert result["status"] == "confirmed"

            stored = db.reservations.find_one({
                "_id": reservation_id
            })

            assert stored["status"] == "confirmed"

            print("REAL HTTP BOOKING CONFIRMATION: PASS")

            # 4. The durable outbox must be committed.
            outbox = list(db.cafe_booking_outbox.find({
                "reservation_id": reservation_id
            }))

            assert len(outbox) == 1, (
                "Expected exactly one committed "
                "booking notification outbox event."
            )

            assert outbox[0]["venue_id"] == venue_id
            assert outbox[0]["customer_id"] == customer_id
            assert outbox[0]["delivery_state"] == "pending"

            print("DURABLE OUTBOX COMMIT: PASS")

            # 5. The same session sees updated state.
            refreshed = http.get(
                "/api/v1/cafe-partners/bookings"
                "?status=confirmed&limit=20",
                headers=auth(own_token),
            )

            assert refreshed.status_code == 200
            assert refreshed.json["count"] == 1

            updated = refreshed.json["bookings"][0]

            assert updated["reservation_id"] == str(
                reservation_id
            )
            assert updated["status"] == "confirmed"

            print("BOOKING LIST REFRESH: PASS")

            # 6. Duplicate confirmation must not
            # produce a second notification.
            repeated = http.post(
                path,
                headers=auth(own_token),
                json={"decision": "confirmed"},
            )

            assert repeated.status_code == 409
            assert db.cafe_booking_outbox.count_documents(
                {"reservation_id": reservation_id}
            ) == 1

            print("DUPLICATE TRANSITION DENIED: PASS")

            # 7. Revoked cafe credentials stop working.
            assert revoke_cafe_session(
                token=own_token
            ) is True

            revoked = http.get(
                "/api/v1/cafe-partners/bookings",
                headers=auth(own_token),
            )

            assert revoked.status_code == 401

            print("CAFE SESSION REVOCATION: PASS")

        print("J22 FULL HTTP MONGODB WORKFLOW: PASS")

    finally:
        try:
            client.drop_database(database_name)
        finally:
            client.close()