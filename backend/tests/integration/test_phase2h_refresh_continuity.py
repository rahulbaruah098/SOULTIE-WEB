"""Real refresh and logout acceptance using an isolated MongoDB database."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bson import ObjectId
from app import create_app
from app.extensions import mongo
from app.repositories.session_repository import create_session
from app.security.tokens import (
    generate_refresh_token,
    generate_session_id,
    generate_token_family_id,
    hash_refresh_token,
    decode_access_token,
)


def test_refresh_survives_reopening_and_logout_revokes_session():
    db_name = "soultie_phase2h_continuity_qa_" + uuid4().hex
    assert db_name.startswith("soultie_phase2h_continuity_qa_")

    app = create_app({
        "TESTING": True,
        "AUTO_ENSURE_INDEXES": False,
        "SOCKETIO_MESSAGE_QUEUE": "",
        "MONGO_URI": "mongodb://localhost:27017/" + db_name,
        "JWT_SECRET_KEY": "isolated-phase2h-continuity-test-key",
        "JWT_ALGORITHM": "HS256",
        "JWT_ISSUER": "xuoroni-backend",
        "JWT_AUDIENCE": "xuoroni-mobile",
        "JWT_ACCESS_TOKEN_MINUTES": 15,
        "JWT_REFRESH_TOKEN_DAYS": 30,
    })

    with app.app_context():
        try:
            now = datetime.now(timezone.utc)
            user_id = ObjectId()
            refresh_token = generate_refresh_token()
            session_id = generate_session_id()
            family_id = generate_token_family_id()

            mongo.db.users.insert_one({
                "_id": user_id,
                "account_status": "active",
                "created_at": now,
                "updated_at": now,
            })

            original_session = create_session(
                user_id=user_id,
                session_id=session_id,
                family_id=family_id,
                refresh_token_hash=hash_refresh_token(refresh_token),
                expires_at=now + timedelta(days=30),
            )

            # A fresh HTTP client simulates reopening the app.
            first_client = app.test_client()
            first = first_client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh_token},
            )
            assert first.status_code == 200, first.get_json()

            tokens = first.get_json()
            assert isinstance(tokens.get("access_token"), str)
            assert isinstance(tokens.get("refresh_token"), str)
            assert tokens["refresh_token"] != refresh_token

            claims = decode_access_token(tokens["access_token"])
            assert claims["sub"] == str(user_id)
            assert claims["sid"] != session_id

            renewed = mongo.db.auth_sessions.find_one({
                "session_id": claims["sid"]
            })
            assert renewed is not None
            assert renewed["revoked_at"] is None
            assert renewed["family_id"] == family_id
            assert renewed["expires_at"] > original_session["expires_at"]

            # Reopening again can renew using the latest refresh token.
            reopened_client = app.test_client()
            second = reopened_client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            assert second.status_code == 200, second.get_json()

            latest = second.get_json()
            assert latest["refresh_token"] != tokens["refresh_token"]

            # Explicit manual logout ends the current session.
            logout = reopened_client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": latest["refresh_token"]},
            )
            assert logout.status_code == 200

            rejected = app.test_client().post(
                "/api/v1/auth/refresh",
                json={"refresh_token": latest["refresh_token"]},
            )
            assert rejected.status_code == 401

        finally:
            # Deletes only the uniquely named disposable QA database.
            assert db_name.startswith("soultie_phase2h_continuity_qa_")
            mongo.cx.drop_database(db_name)
