"""Final cross-feature hardening tests for Xuoroni realtime Chat."""

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
import app.realtime.chat as realtime_chat
from app.realtime.notifications import (
    RealtimeNotificationRoutingError,
)
from app.services.auth_service import (
    issue_session,
)
from app.services.chat_service import (
    ensure_chat_for_match,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_chat_hardening"
)

TEST_PREFIX = (
    "xuoroni:test:realtime-chat-hardening"
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
                "xuoroni-chat-hardening-"
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
                "realtime-chat-hardening"
            ),
        },
    )

    return (
        user_id,
        tokens,
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

    chat = ensure_chat_for_match(
        user_id=sender_id,
        match_id=match_id,
    )

    return {
        "match_id": match_id,
        "sender_id": sender_id,
        "sender_tokens": sender_tokens,
        "recipient_id": recipient_id,
        "recipient_tokens": recipient_tokens,
        "conversation_id": (
            chat[
                "conversation"
            ][
                "id"
            ]
        ),
    }


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


def _clear(
    *clients,
):
    for client in clients:
        if (
            client is not None
            and client.is_connected(
                "/"
            )
        ):
            client.get_received(
                namespace="/"
            )


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


def _send(
    sender,
    state,
    *,
    client_message_id,
    body="Hardening message",
):
    return sender.emit(
        "message.send",
        {
            "conversation_id": (
                state[
                    "conversation_id"
                ]
            ),
            "client_message_id": (
                client_message_id
            ),
            "body": body,
            "message_type": "text",
        },
        namespace="/",
        callback=True,
    )


def test_read_syncs_unread_state_to_all_reader_devices(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

    recipient_one = _connect(
        app,
        state[
            "recipient_tokens"
        ],
    )

    recipient_two = _connect(
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
        _clear(
            sender,
            recipient_one,
            recipient_two,
        )

        sent = _send(
            sender,
            state,
            client_message_id=(
                "hardening-read-sync-001"
            ),
        )

        assert sent[
            "ok"
        ] is True

        assert sent[
            "created"
        ] is True

        _clear(
            sender,
            recipient_one,
            recipient_two,
        )

        read = recipient_one.emit(
            "message.read",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "through_sequence": 1,
            },
            namespace="/",
            callback=True,
        )

        assert read[
            "ok"
        ] is True

        assert read[
            "modified_count"
        ] > 0

        first_updates = _events(
            recipient_one,
            "conversation.updated",
        )

        second_updates = _events(
            recipient_two,
            "conversation.updated",
        )

        assert len(
            first_updates
        ) == 1

        assert len(
            second_updates
        ) == 1

        for event in (
            first_updates[
                0
            ],
            second_updates[
                0
            ],
        ):
            payload = (
                event[
                    "args"
                ][
                    0
                ]
            )

            assert (
                payload[
                    "reason"
                ]
                == "message.read"
            )

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
                == 0
            )

            assert (
                payload[
                    "latest_message"
                ]
                is None
            )

        # The sender does not own the reader's personalized
        # unread state.
        assert (
            _events(
                sender,
                "conversation.updated",
            )
            == []
        )

    finally:
        _disconnect(
            sender,
            recipient_one,
            recipient_two,
        )


def test_repeated_read_does_not_duplicate_private_state_update(
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
        _clear(
            sender,
            recipient,
        )

        sent = _send(
            sender,
            state,
            client_message_id=(
                "hardening-read-idempotent"
            ),
        )

        assert sent[
            "created"
        ] is True

        _clear(
            sender,
            recipient,
        )

        first = recipient.emit(
            "message.read",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "through_sequence": 1,
            },
            namespace="/",
            callback=True,
        )

        assert first[
            "modified_count"
        ] > 0

        assert len(
            _events(
                recipient,
                "conversation.updated",
            )
        ) == 1

        second = recipient.emit(
            "message.read",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "through_sequence": 1,
            },
            namespace="/",
            callback=True,
        )

        assert second[
            "ok"
        ] is True

        assert second[
            "modified_count"
        ] == 0

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


def test_block_added_after_connection_prevents_message_and_update(
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
        _clear(
            sender,
            recipient,
        )

        with app.app_context():
            mongo.db.blocks.insert_one(
                {
                    "_id": ObjectId(),
                    "blocker_id": (
                        state[
                            "recipient_id"
                        ]
                    ),
                    "blocked_id": (
                        state[
                            "sender_id"
                        ]
                    ),
                    "created_at": (
                        datetime.now(
                            timezone.utc
                        )
                    ),
                }
            )

        response = _send(
            sender,
            state,
            client_message_id=(
                "hardening-blocked"
            ),
        )

        assert response[
            "ok"
        ] is False

        assert (
            _events(
                recipient,
                "conversation.updated",
            )
            == []
        )

        with app.app_context():
            assert (
                mongo.db.messages.count_documents(
                    {
                        "client_message_id": (
                            "hardening-blocked"
                        ),
                    }
                )
                == 0
            )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_unmatch_after_connection_prevents_message_and_update(
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
        _clear(
            sender,
            recipient,
        )

        with app.app_context():
            mongo.db.matches.update_one(
                {
                    "_id": (
                        state[
                            "match_id"
                        ]
                    ),
                },
                {
                    "$set": {
                        "status": "unmatched",
                        "unmatched_at": (
                            datetime.now(
                                timezone.utc
                            )
                        ),
                    }
                },
            )

        response = _send(
            sender,
            state,
            client_message_id=(
                "hardening-unmatched"
            ),
        )

        assert response[
            "ok"
        ] is False

        assert (
            _events(
                recipient,
                "conversation.updated",
            )
            == []
        )

        with app.app_context():
            assert (
                mongo.db.messages.count_documents(
                    {
                        "client_message_id": (
                            "hardening-unmatched"
                        ),
                    }
                )
                == 0
            )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_revoked_session_after_connection_prevents_message(
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
        _clear(
            sender,
            recipient,
        )

        with app.app_context():
            mongo.db.auth_sessions.update_one(
                {
                    "session_id": (
                        state[
                            "sender_tokens"
                        ][
                            "session_id"
                        ]
                    ),
                },
                {
                    "$set": {
                        "revoked_at": (
                            datetime.now(
                                timezone.utc
                            )
                        ),
                        "revoke_reason": (
                            "hardening-test"
                        ),
                    }
                },
            )

        response = _send(
            sender,
            state,
            client_message_id=(
                "hardening-revoked"
            ),
        )

        assert response[
            "ok"
        ] is False

        assert (
            _events(
                recipient,
                "conversation.updated",
            )
            == []
        )

        with app.app_context():
            assert (
                mongo.db.messages.count_documents(
                    {
                        "client_message_id": (
                            "hardening-revoked"
                        ),
                    }
                )
                == 0
            )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_private_notification_failure_does_not_undo_durable_message(
    app,
    monkeypatch,
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

    def fail_notification(
        **_kwargs,
    ):
        raise (
            RealtimeNotificationRoutingError()
        )

    monkeypatch.setattr(
        realtime_chat,
        "emit_conversation_update",
        fail_notification,
    )

    try:
        _clear(
            sender,
            recipient,
        )

        response = _send(
            sender,
            state,
            client_message_id=(
                "hardening-notification-failure"
            ),
        )

        assert response[
            "ok"
        ] is True

        assert response[
            "created"
        ] is True

        assert (
            _events(
                recipient,
                "conversation.updated",
            )
            == []
        )

        with app.app_context():
            assert (
                mongo.db.messages.count_documents(
                    {
                        "client_message_id": (
                            "hardening-notification-failure"
                        ),
                    }
                )
                == 1
            )

    finally:
        _disconnect(
            sender,
            recipient,
        )


def test_private_notification_failure_does_not_undo_read_receipt(
    app,
    monkeypatch,
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
        _clear(
            sender,
            recipient,
        )

        sent = _send(
            sender,
            state,
            client_message_id=(
                "hardening-read-routing-failure"
            ),
        )

        assert sent[
            "created"
        ] is True

        _clear(
            sender,
            recipient,
        )

        def fail_notification(
            **_kwargs,
        ):
            raise (
                RealtimeNotificationRoutingError()
            )

        monkeypatch.setattr(
            realtime_chat,
            "emit_conversation_update",
            fail_notification,
        )

        read = recipient.emit(
            "message.read",
            {
                "conversation_id": (
                    state[
                        "conversation_id"
                    ]
                ),
                "through_sequence": 1,
            },
            namespace="/",
            callback=True,
        )

        assert read[
            "ok"
        ] is True

        assert read[
            "modified_count"
        ] > 0

        assert (
            read[
                "conversation"
            ][
                "unread_count"
            ]
            == 0
        )

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