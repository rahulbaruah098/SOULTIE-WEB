"""Phase 2H real JWT and Socket.IO security acceptance.

Real JWT encode/decode and Socket.IO transport.
Mock repositories isolate this suite from MongoDB.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from bson import ObjectId

from app import create_app
from app.extensions import socketio
from app.security.tokens import create_access_token
import app.realtime as realtime
import app.realtime.auth as realtime_auth
import app.realtime.notifications as notifications


@pytest.fixture
def qa(monkeypatch):
    app = create_app({
        "TESTING": True,
        "AUTO_ENSURE_INDEXES": False,
        "SOCKETIO_MESSAGE_QUEUE": "",
        "MONGO_URI": "mongodb://localhost:27017/soultie_phase2h_jwt_qa",
        "JWT_SECRET_KEY": "phase2h-isolated-test-secret-not-for-production",
        "JWT_ALGORITHM": "HS256",
        "JWT_ISSUER": "xuoroni-backend",
        "JWT_AUDIENCE": "xuoroni-mobile",
        "JWT_ACCESS_TOKEN_MINUTES": 15,
    })

    users = [ObjectId(), ObjectId(), ObjectId()]
    sessions = {}

    for index, user_id in enumerate(users):
        sid = "phase2h-session-" + str(index)
        sessions[sid] = {
            "session_id": sid,
            "user_id": user_id,
            "revoked_at": None,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ),
        }

    def lookup_session(sid):
        return sessions.get(sid)

    def lookup_user(user_id):
        if user_id in users:
            return {
                "_id": user_id,
                "account_status": "active",
            }
        return None

    monkeypatch.setattr(
        realtime_auth, "get_session_by_id", lookup_session
    )
    monkeypatch.setattr(
        realtime_auth, "get_user_by_id", lookup_user
    )

    monkeypatch.setattr(
        realtime, "mark_socket_online",
        lambda **kwargs: {"changed": False},
    )
    monkeypatch.setattr(
        realtime, "remove_socket_presence",
        lambda **kwargs: {"changed": False},
    )
    monkeypatch.setattr(
        realtime, "refresh_socket_presence",
        lambda **kwargs: {"changed": False},
    )

    monkeypatch.setattr(
        notifications, "pair_is_blocked",
        lambda *args: False,
    )
    monkeypatch.setattr(
        notifications, "_observer_is_available",
        lambda user_id: str(user_id) in {
            str(item) for item in users
        },
    )

    with app.app_context():
        tokens = [
            create_access_token(
                str(user_id),
                session_id="phase2h-session-" + str(index),
            )
            for index, user_id in enumerate(users)
        ]

        yield app, users, sessions, tokens


def connect(app, token):
    return socketio.test_client(
        app,
        namespace="/",
        auth={"access_token": token},
    )


def test_real_jwt_private_match_delivery(qa):
    app, users, sessions, tokens = qa
    clients = []

    try:
        for token in tokens:
            client = connect(app, token)
            assert client.is_connected("/")
            clients.append(client)

        first, second, outsider = clients

        for recipient, peer in (
            (users[0], users[1]),
            (users[1], users[0]),
        ):
            notifications.emit_match_created(
                user_id=recipient,
                match={
                    "id": "phase2h-match",
                    "other_user_id": str(peer),
                    "status": "active",
                },
            )

        def received(client):
            return [
                event["args"][0]
                for event in client.get_received("/")
                if event["name"] == "match.created"
            ]

        first_events = received(first)
        second_events = received(second)
        outsider_events = received(outsider)

        assert len(first_events) == 1
        assert len(second_events) == 1
        assert outsider_events == []

        assert (
            first_events[0]["match"]["other_user_id"]
            == str(users[1])
        )
        assert (
            second_events[0]["match"]["other_user_id"]
            == str(users[0])
        )

        assert (
            first_events[0]["experience_available"]
            is True
        )

    finally:
        for client in clients:
            if client.is_connected("/"):
                client.disconnect("/")


def test_wrong_signature_is_rejected(qa):
    app, users, sessions, tokens = qa

    payload = jwt.decode(
        tokens[0],
        options={"verify_signature": False},
    )

    forged = jwt.encode(
        payload,
        "incorrect-signing-key",
        algorithm="HS256",
    )

    client = connect(app, forged)
    assert not client.is_connected("/")


def test_expired_jwt_is_rejected(qa):
    app, users, sessions, tokens = qa

    payload = jwt.decode(
        tokens[0],
        options={"verify_signature": False},
    )
    now = datetime.now(timezone.utc)
    payload["iat"] = int(
        (now - timedelta(hours=2)).timestamp()
    )
    payload["exp"] = int(
        (now - timedelta(hours=1)).timestamp()
    )

    expired = jwt.encode(
        payload,
        app.config["JWT_SECRET_KEY"],
        algorithm=app.config["JWT_ALGORITHM"],
    )

    client = connect(app, expired)
    assert not client.is_connected("/")


def test_session_revocation_blocks_protected_socket_event(qa):
    app, users, sessions, tokens = qa

    client = connect(app, tokens[0])
    assert client.is_connected("/")

    try:
        before = client.emit(
            "presence.heartbeat",
            callback=True,
            namespace="/",
        )
        assert before["ok"] is True

        sessions["phase2h-session-0"]["revoked_at"] = (
            datetime.now(timezone.utc)
        )

        after = client.emit(
            "presence.heartbeat",
            callback=True,
            namespace="/",
        )

        assert after["ok"] is False
        assert (
            after["error"]["code"]
            == "INVALID_ACCESS_TOKEN"
        )
    finally:
        if client.is_connected("/"):
            client.disconnect("/")
