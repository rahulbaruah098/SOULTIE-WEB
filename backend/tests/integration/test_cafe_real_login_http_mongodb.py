"""Real cafe partner login, session and logout HTTP acceptance."""

import os
from contextlib import ExitStack
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from pymongo import MongoClient
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import mongo


BASE = "/api/v1/cafe-partners"


def test_cafe_password_login_and_logout():
    uri = os.getenv(
        "XUORONI_QA_MONGO_URI",
        "mongodb://127.0.0.1:27017/",
    )

    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=5000,
    )

    database_name = "xuoroni_qa_j23_" + uuid4().hex

    try:
        client.admin.command("ping")
        db = client[database_name]

        partner_id = ObjectId()
        venue_id = ObjectId()

        email = (
            "j23-" + uuid4().hex + "@example.invalid"
        )
        password = "J23_Synthetic_QA_Password_2026!"

        db.venues.insert_one({
            "_id": venue_id,
            "owner_id": str(partner_id),
            "status": "approved",
            "active": True,
            "venue_name": "J23 Test Cafe",
            "city": "Guwahati",
            "facilities": ["wifi"],
            "reservation_fence_version": 0,
        })

        db.cafe_partner_accounts.insert_one({
            "_id": partner_id,
            "account_type": "cafe_partner",
            "status": "approved",
            "email_verified": True,
            "email": email,
            "password_hash": generate_password_hash(
                password,
                method="scrypt",
            ),
            "venue_id": venue_id,
        })

        app = create_app({"TESTING": True})

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(mongo, "db", db)
            )
            stack.enter_context(
                patch.object(mongo, "cx", client)
            )

            http = app.test_client()

            # Incorrect credentials must not issue a token.
            wrong = http.post(
                BASE + "/login",
                json={
                    "email": email,
                    "password": "Incorrect-QA-Password",
                },
            )

            assert wrong.status_code in (400, 401, 403), (
                f"Wrong-password HTTP {wrong.status_code}"
            )
            assert "access_token" not in (
                wrong.get_json() or {}
            )

            print("INCORRECT PASSWORD DENIED: PASS")

            # Real password authentication through HTTP.
            login = http.post(
                BASE + "/login",
                json={
                    "email": email,
                    "password": password,
                },
            )

            assert login.status_code == 200, (
                f"Login HTTP {login.status_code}: "
                f"{(login.get_json() or {}).get('error')}"
            )

            identity = login.get_json()

            assert identity["account_type"] == "cafe_partner"
            assert identity["token_type"] == "Bearer"
            assert identity["partner_id"] == str(partner_id)
            assert identity["venue_id"] == str(venue_id)

            token = identity["access_token"]
            assert isinstance(token, str) and len(token) >= 47

            print("REAL PASSWORD LOGIN: PASS")

            # The stored session must contain only a digest.
            stored_session = db.cafe_partner_sessions.find_one({
                "partner_id": partner_id,
                "venue_id": venue_id,
                "revoked_at": None,
            })

            assert stored_session is not None
            assert stored_session.get("token_hash")
            assert token not in str(stored_session)

            headers = {
                "Authorization": "Bearer " + token
            }

            # Current-session identity.
            profile = http.get(
                BASE + "/me",
                headers=headers,
            )

            assert profile.status_code == 200, (
                f"Profile HTTP {profile.status_code}"
            )

            account = profile.get_json()
            assert account["account_type"] == "cafe_partner"
            assert account["partner_id"] == str(partner_id)
            assert account["venue_id"] == str(venue_id)

            print("SESSION PROFILE VERIFICATION: PASS")

            # Authenticated venue data for React dashboard.
            venue = http.get(
                BASE + "/venue",
                headers=headers,
            )

            assert venue.status_code == 200, (
                f"Venue HTTP {venue.status_code}: "
                f"{(venue.get_json() or {}).get('error')}"
            )

            venue_data = venue.get_json()
            assert venue_data["venue"]["venue_id"] == str(venue_id)
            assert venue_data["venue"]["venue_name"] == "J23 Test Cafe"

            print("AUTHENTICATED VENUE: PASS")

            # Logout must revoke the bearer credential.
            logout = http.post(
                BASE + "/logout",
                headers=headers,
            )

            assert logout.status_code == 200, (
                f"Logout HTTP {logout.status_code}"
            )

            after_logout = http.get(
                BASE + "/me",
                headers=headers,
            )

            assert after_logout.status_code == 401

            print("LOGOUT SESSION REVOCATION: PASS")

            # Suspended café account must not log in.
            db.cafe_partner_accounts.update_one(
                {"_id": partner_id},
                {"$set": {"status": "suspended"}},
            )

            suspended = http.post(
                BASE + "/login",
                json={
                    "email": email,
                    "password": password,
                },
            )

            assert suspended.status_code in (400, 401, 403)
            assert "access_token" not in (
                suspended.get_json() or {}
            )

            print("SUSPENDED PARTNER DENIED: PASS")

        print("J23 REAL LOGIN AND SESSION FLOW: PASS")

    finally:
        try:
            client.drop_database(database_name)
        finally:
            client.close()