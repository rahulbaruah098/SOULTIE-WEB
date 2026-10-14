"""Integration tests for private realtime conversation updates."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.extensions import (
    mongo,
    redis_store,
    socketio,
)
from app.services.auth_service import (
    issue_session,
)
from app.services.chat_service import (
    ensure_chat_for_match,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_conversation_updates"
)

TEST_PREFIX = (
    "xuoroni:test:conversation-updates"
)


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-conversation-update-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": "xuoroni-test-client",
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "REALTIME_PRESENCE_KEY_PREFIX": (
                TEST_PREFIX
            ),
            "REALTIME_PRESENCE_TTL_SECONDS": 20,
            "REALTIME_PRESENCE_HEARTBEAT_SECONDS": 5,
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        redis = (
            redis_store.get_client()
        )

        keys = list(
            redis.scan_iter(
                match=f"{TEST_PREFIX}:*"
            )
        )

        if keys:
            redis.delete(
                *keys
            )

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )

        keys = list(
            redis.scan_iter(
                match=f"{TEST_PREFIX}:*"
            )
        )

        if keys:
            redis.delete(
                *keys
            )


def _create_user():
    user_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    mongo.db.profiles.insert_one(
        {
            "_id": ObjectId(),
            "user_id": user_id,
            "onboarding_status": "completed",
            "profile_status": "active",
            "visibility": "visible",
            "created_at": now,
            "updated_at": now,
        }
    )

    tokens = issue_session(
        user_id,
        metadata={
            "source": (
                "conversation-update-test"
            ),
        },
    )

    return (
        user_id,
        tokens,
    )


def _connect(
    app,
    tokens,
):
    return socketio.test_client(
        app,
        namespace="/",
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )


def _events(
    client,
    name,
):
    return [
        event
        for event
        in client.get_received(
            namespace="/"
        )
        if event.get(
            "name"
        )
        == name
    ]


def _disconnect(
    *clients,
):
    for client in clients:
        if (
            client is not None
            and client.is_connected(
                "/"
            )
        ):
            client.disconnect(
                namespace="/"
            )


def _prepare_conversation():
    sender_id, sender_tokens = (
        _create_user()
    )

    recipient_id, recipient_tokens = (
        _create_user()
    )

    match_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    mongo.db.matches.insert_one(
        {
            "_id": match_id,
            "user_ids": [
                sender_id,
                recipient_id,
            ],
            "status": "active",
            "matched_at": now,
            "updated_at": now,
        }
    )

    result = ensure_chat_for_match(
        user_id=sender_id,
        match_id=match_id,
    )

    return {
        "sender_id": sender_id,
        "sender_tokens": sender_tokens,
        "recipient_id": recipient_id,
        "recipient_tokens": recipient_tokens,
        "conversation_id": (
            result[
                "conversation"
            ][
                "id"
            ]
        ),
    }


def test_recipient_gets_unread_update_without_joining_chat_room(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

    recipient = _connect(
        app,
        state[
            "recipient_tokens"
        ],
    )

    sender = _connect(
        app,
        state[
            "sender_tokens"
        ],
    )

    try:
        # Clear presence transitions from connection setup.
        recipient.get_received(
            namespace="/"
        )

        response = sender.emit(
            "message.send",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "client_message_id": (
                    "conversation-update-001"
                ),
                "body": (
                    "Hello from private update"
                ),
                "message_type": "text",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert response[
            "created"
        ] is True

        updates = _events(
            recipient,
            "conversation.updated",
        )

        assert len(
            updates
        ) == 1

        payload = (
            updates[
                0
            ][
                "args"
            ][
                0
            ]
        )

        assert payload[
            "reason"
        ] == "message.new"

        assert (
            payload[
                "conversation"
            ][
                "id"
            ]
            == state[
                "conversation_id"
            ]
        )

        assert (
            payload[
                "conversation"
            ][
                "unread_count"
            ]
            == 1
        )

        assert (
            payload[
                "conversation"
            ][
                "last_sequence"
            ]
            == 1
        )

        assert (
            payload[
                "latest_message"
            ][
                "body"
            ]
            == (
                "Hello from private update"
            )
        )

        assert (
            payload[
                "latest_message"
            ][
                "recipient_id"
            ]
            == str(
                state[
                    "recipient_id"
                ]
            )
        )

        # The recipient never joined the conversation room,
        # therefore message.new itself must not arrive here.
        assert (
            _events(
                recipient,
                "message.new",
            )
            == []
        )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_idempotent_retry_does_not_duplicate_conversation_update(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

    recipient = _connect(
        app,
        state[
            "recipient_tokens"
        ],
    )

    sender = _connect(
        app,
        state[
            "sender_tokens"
        ],
    )

    try:
        recipient.get_received(
            namespace="/"
        )

        payload = {
            "conversation_id": (
                state[
                    "conversation_id"
                ]
            ),
            "client_message_id": (
                "conversation-update-idempotent"
            ),
            "body": "Only once",
            "message_type": "text",
        }

        first = sender.emit(
            "message.send",
            payload,
            namespace="/",
            callback=True,
        )

        assert first[
            "created"
        ] is True

        assert len(
            _events(
                recipient,
                "conversation.updated",
            )
        ) == 1

        second = sender.emit(
            "message.send",
            payload,
            namespace="/",
            callback=True,
        )

        assert second[
            "ok"
        ] is True

        assert second[
            "created"
        ] is False

        assert (
            _events(
                recipient,
                "conversation.updated",
            )
            == []
        )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_unrelated_connected_user_receives_no_conversation_update(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

        _, outsider_tokens = (
            _create_user()
        )

    recipient = _connect(
        app,
        state[
            "recipient_tokens"
        ],
    )

    outsider = _connect(
        app,
        outsider_tokens,
    )

    sender = _connect(
        app,
        state[
            "sender_tokens"
        ],
    )

    try:
        recipient.get_received(
            namespace="/"
        )

        outsider.get_received(
            namespace="/"
        )

        response = sender.emit(
            "message.send",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "client_message_id": (
                    "conversation-update-private"
                ),
                "body": "Private message",
                "message_type": "text",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "created"
        ] is True

        assert len(
            _events(
                recipient,
                "conversation.updated",
            )
        ) == 1

        assert (
            _events(
                outsider,
                "conversation.updated",
            )
            == []
        )

    finally:
        _disconnect(
            sender,
            recipient,
            outsider,
        )


def test_conversation_updated_is_outbound_only(
    app,
):
    handlers = (
        socketio.server.handlers.get(
            "/",
            {},
        )
    )

    assert (
        "conversation.updated"
        not in handlers
    )