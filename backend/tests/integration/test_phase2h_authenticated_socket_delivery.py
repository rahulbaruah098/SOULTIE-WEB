"""Authenticated-session Socket.IO delivery for Phase 2H."""

import pytest
from app import create_app
from app.extensions import socketio
import app.realtime as realtime
from app.realtime.notifications import private_user_room_name


USER_A = "507f191e810c19729de860ea"
USER_B = "507f191e810c19729de860eb"
OUTSIDER = "507f191e810c19729de860ec"

TOKENS = {
    "qa-token-a": (USER_A, "qa-session-a"),
    "qa-token-b": (USER_B, "qa-session-b"),
    "qa-token-c": (OUTSIDER, "qa-session-c"),
}


@pytest.fixture
def connected_clients(monkeypatch):
    app = create_app({
        "TESTING": True,
        "AUTO_ENSURE_INDEXES": False,
        "SOCKETIO_MESSAGE_QUEUE": "",
        "MONGO_URI": (
            "mongodb://localhost:27017/"
            "soultie_phase2h_step60_qa"
        ),
    })

    def authenticate(auth):
        token = auth.get("token") if isinstance(auth, dict) else None

        if token not in TOKENS:
            raise realtime.RealtimeAuthError(
                "INVALID_ACCESS_TOKEN",
                "Invalid QA token.",
            )

        user_id, session_id = TOKENS[token]
        return {
            "user_id": user_id,
            "session_id": session_id,
        }

    monkeypatch.setattr(
        realtime,
        "authenticate_realtime_connection",
        authenticate,
    )
    monkeypatch.setattr(
        realtime,
        "mark_socket_online",
        lambda **kwargs: {"changed": False},
    )
    monkeypatch.setattr(
        realtime,
        "remove_socket_presence",
        lambda **kwargs: {"changed": False},
    )

    clients = []

    with app.app_context():
        try:
            for token in TOKENS:
                client = socketio.test_client(
                    app,
                    namespace="/",
                    auth={"token": token},
                )
                assert client.is_connected(namespace="/"), token
                clients.append(client)

            yield app, clients

        finally:
            for client in clients:
                if client.is_connected(namespace="/"):
                    client.disconnect(namespace="/")


def test_each_member_receives_only_own_match_event(connected_clients):
    app, clients = connected_clients
    first, second, outsider = clients

    with app.app_context():
        for recipient, peer in ((USER_A, USER_B), (USER_B, USER_A)):
            socketio.emit(
                "match.created",
                {
                    "event": "match.created",
                    "match": {
                        "id": "qa-match",
                        "other_user_id": peer,
                    },
                    "experience_available": True,
                },
                to=private_user_room_name(recipient),
                namespace="/",
            )

    def events(client):
        return [
            event
            for event in client.get_received(namespace="/")
            if event["name"] == "match.created"
        ]

    events_a = events(first)
    events_b = events(second)
    events_c = events(outsider)

    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_c == []

    assert events_a[0]["args"][0]["match"]["other_user_id"] == USER_B
    assert events_b[0]["args"][0]["match"]["other_user_id"] == USER_A


def test_invalid_credentials_cannot_connect(connected_clients):
    app, _ = connected_clients

    with app.app_context():
        invalid = socketio.test_client(
            app,
            namespace="/",
            auth={"token": "invalid-token"},
        )
        assert not invalid.is_connected(namespace="/")
