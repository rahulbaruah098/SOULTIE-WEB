"""Phase 2H refresh-token replay and family revocation acceptance."""

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
)


def test_replayed_refresh_token_revokes_entire_session_family():
    db_name = "soultie_phase2h_replay_qa_" + uuid4().hex

    app = create_app({
        "TESTING": True,
        "AUTO_ENSURE_INDEXES": False,
        "SOCKETIO_MESSAGE_QUEUE": "",
        "MONGO_URI": "mongodb://localhost:27017/" + db_name,
        "JWT_SECRET_KEY": "isolated-phase2h-replay-test-key",
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
            original_token = generate_refresh_token()
            family_id = generate_token_family_id()

            mongo.db.users.insert_one({
                "_id": user_id,
                "account_status": "active",
                "created_at": now,
                "updated_at": now,
            })

            create_session(
                user_id=user_id,
                session_id=generate_session_id(),
                family_id=family_id,
                refresh_token_hash=hash_refresh_token(original_token),
                expires_at=now + timedelta(days=30),
            )

            client = app.test_client()

            first = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": original_token},
            )

            assert first.status_code == 200, first.get_json()
            new_token = first.get_json()["refresh_token"]

            assert new_token != original_token

            old_session = mongo.db.auth_sessions.find_one({
                "refresh_token_hash": hash_refresh_token(original_token)
            })

            assert old_session["revoked_at"] is not None
            assert old_session["revoke_reason"] == "rotated"

            replay = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": original_token},
            )

            assert replay.status_code == 401, replay.get_json()

            # Verify actual database revocation, not response formatting.
            sessions = list(mongo.db.auth_sessions.find({
                "family_id": family_id
            }))

            assert len(sessions) >= 2
            assert all(
                session.get("revoked_at") is not None
                for session in sessions
            )

            assert any(
                session.get("revoke_reason") == "refresh_token_reuse"
                for session in sessions
            )

            newest = client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": new_token},
            )

            assert newest.status_code == 401, newest.get_json()

            print(
                "TOKEN_REPLAY_FAMILY_REVOCATION_VERIFIED",
                flush=True,
            )

        finally:
            assert db_name.startswith("soultie_phase2h_replay_qa_")
            mongo.cx.drop_database(db_name)
