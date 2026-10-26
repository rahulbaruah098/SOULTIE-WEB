"""Integration tests for Soultie realtime Chat messages."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import (
    mongo,
    socketio,
)
import app.realtime.chat as realtime_chat
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_chat_messages"
)


class _MessageServiceError(
    RuntimeError
):
    def __init__(
        self,
        code,
        message,
        *,
        status_code=400,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message
        self.status_code = (
            status_code
        )


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "SECRET_KEY": (
                "xuoroni-realtime-message-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-message-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
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

    return mongo.db.users.find_one(
        {
            "_id": user_id,
        }
    )


def _issue(
    user,
):
    return issue_session(
        user[
            "_id"
        ],
        metadata={
            "source": (
                "realtime-chat-message-test"
            ),
        },
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


def _conversation_payload(
    conversation_id,
    *,
    other_user_id=None,
):
    return {
        "id": str(
            conversation_id
        ),
        "match_id": str(
            ObjectId()
        ),
        "status": "active",
        "other_user_id": str(
            other_user_id
            or ObjectId()
        ),
        "unread_count": 0,
        "last_read_sequence": 0,
        "last_delivered_sequence": 0,
        "last_sequence": 0,
        "last_message_id": None,
        "last_message_at": None,
        "last_message_preview": None,
        "created_at": (
            "2026-10-14T00:00:00+00:00"
        ),
        "updated_at": (
            "2026-10-14T00:00:00+00:00"
        ),
    }


def _message_payload(
    *,
    conversation_id,
    sender_id,
    recipient_id,
    client_message_id="client-message-001",
    sequence=1,
    body="Hello from realtime",
):
    return {
        "id": str(
            ObjectId()
        ),
        "conversation_id": str(
            conversation_id
        ),
        "match_id": str(
            ObjectId()
        ),
        "sender_id": str(
            sender_id
        ),
        "recipient_id": str(
            recipient_id
        ),
        "sequence": sequence,
        "client_message_id": (
            client_message_id
        ),
        "message_type": "text",
        "body": body,
        "delivery_status": "sent",
        "sent_at": (
            "2026-10-14T00:00:00+00:00"
        ),
        "delivered_at": None,
        "read_at": None,
        "moderation_status": (
            "not_reviewed"
        ),
        "deleted_at": None,
    }


def _install_room_authorizer(
    monkeypatch,
    *,
    conversation_id,
    allowed_user_ids,
):
    allowed = {
        str(
            user_id
        )
        for user_id
        in allowed_user_ids
    }

    expected_conversation_id = str(
        conversation_id
    )

    def fake_get_my_conversation(
        *,
        user_id,
        conversation_id,
    ):
        if (
            str(
                user_id
            )
            not in allowed
            or str(
                conversation_id
            )
            != expected_conversation_id
        ):
            raise AssertionError(
                (
                    "Unexpected conversation "
                    "authorization request."
                )
            )

        return _conversation_payload(
            expected_conversation_id
        )

    monkeypatch.setattr(
        realtime_chat,
        "get_my_conversation",
        fake_get_my_conversation,
    )


def _join(
    client,
    conversation_id,
):
    response = client.emit(
        "chat:join",
        {
            "conversation_id": str(
                conversation_id
            ),
        },
        namespace="/",
        callback=True,
    )

    assert response[
        "ok"
    ] is True


def _message_events(
    client,
):
    received = client.get_received(
        namespace="/"
    )

    return [
        packet
        for packet in received
        if packet.get(
            "name"
        )
        == "message.new"
    ]


def test_new_message_broadcasts_to_joined_room_members(
    app,
    monkeypatch,
):
    with app.app_context():
        sender = _create_user()
        recipient = _create_user()

        sender_tokens = _issue(
            sender
        )

        recipient_tokens = _issue(
            recipient
        )

    conversation_id = ObjectId()

    _install_room_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            sender[
                "_id"
            ],
            recipient[
                "_id"
            ],
        },
    )

    expected_message = (
        _message_payload(
            conversation_id=(
                conversation_id
            ),
            sender_id=sender[
                "_id"
            ],
            recipient_id=recipient[
                "_id"
            ],
        )
    )

    calls = []

    def fake_send_chat_message(
        *,
        user_id,
        conversation_id,
        client_message_id,
        body,
        message_type,
    ):
        calls.append(
            {
                "user_id": str(
                    user_id
                ),
                "conversation_id": str(
                    conversation_id
                ),
                "client_message_id": (
                    client_message_id
                ),
                "body": body,
                "message_type": (
                    message_type
                ),
            }
        )

        return {
            "created": True,
            "message": expected_message,
        }

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        fake_send_chat_message,
    )

    sender_client = _connect(
        app,
        sender_tokens,
    )

    recipient_client = _connect(
        app,
        recipient_tokens,
    )

    try:
        _join(
            sender_client,
            conversation_id,
        )

        _join(
            recipient_client,
            conversation_id,
        )

        response = sender_client.emit(
            "message.send",
            {
                "conversation_id": str(
                    conversation_id
                ),
                "client_message_id": (
                    "client-message-001"
                ),
                "body": (
                    "Hello from realtime"
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

        assert response[
            "message"
        ] == expected_message

        assert len(
            calls
        ) == 1

        assert (
            calls[
                0
            ][
                "user_id"
            ]
            == str(
                sender[
                    "_id"
                ]
            )
        )

        sender_events = (
            _message_events(
                sender_client
            )
        )

        recipient_events = (
            _message_events(
                recipient_client
            )
        )

        assert len(
            sender_events
        ) == 1

        assert len(
            recipient_events
        ) == 1

        assert (
            sender_events[
                0
            ][
                "args"
            ][
                0
            ][
                "message"
            ]
            == expected_message
        )

        assert (
            recipient_events[
                0
            ][
                "args"
            ][
                0
            ][
                "message"
            ]
            == expected_message
        )

    finally:
        if sender_client.is_connected(
            "/"
        ):
            sender_client.disconnect(
                namespace="/"
            )

        if recipient_client.is_connected(
            "/"
        ):
            recipient_client.disconnect(
                namespace="/"
            )


def test_socket_outside_room_does_not_receive_message_new(
    app,
    monkeypatch,
):
    with app.app_context():
        sender = _create_user()
        recipient = _create_user()
        outsider = _create_user()

        sender_tokens = _issue(
            sender
        )

        recipient_tokens = _issue(
            recipient
        )

        outsider_tokens = _issue(
            outsider
        )

    conversation_id = ObjectId()

    _install_room_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            sender[
                "_id"
            ],
            recipient[
                "_id"
            ],
        },
    )

    expected_message = (
        _message_payload(
            conversation_id=(
                conversation_id
            ),
            sender_id=sender[
                "_id"
            ],
            recipient_id=recipient[
                "_id"
            ],
        )
    )

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        lambda **_kwargs: {
            "created": True,
            "message": expected_message,
        },
    )

    sender_client = _connect(
        app,
        sender_tokens,
    )

    recipient_client = _connect(
        app,
        recipient_tokens,
    )

    outsider_client = _connect(
        app,
        outsider_tokens,
    )

    try:
        _join(
            sender_client,
            conversation_id,
        )

        _join(
            recipient_client,
            conversation_id,
        )

        response = sender_client.emit(
            "message.send",
            {
                "conversation_id": str(
                    conversation_id
                ),
                "client_message_id": "m-1",
                "body": "Hello",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert len(
            _message_events(
                sender_client
            )
        ) == 1

        assert len(
            _message_events(
                recipient_client
            )
        ) == 1

        assert (
            _message_events(
                outsider_client
            )
            == []
        )

    finally:
        for client in (
            sender_client,
            recipient_client,
            outsider_client,
        ):
            if client.is_connected(
                "/"
            ):
                client.disconnect(
                    namespace="/"
                )


def test_client_cannot_forge_sender_identity(
    app,
    monkeypatch,
):
    with app.app_context():
        sender = _create_user()
        victim = _create_user()

        sender_tokens = _issue(
            sender
        )

    conversation_id = ObjectId()

    captured = {}

    def fake_send_chat_message(
        *,
        user_id,
        conversation_id,
        client_message_id,
        body,
        message_type,
    ):
        captured.update(
            {
                "user_id": str(
                    user_id
                ),
                "conversation_id": str(
                    conversation_id
                ),
                "client_message_id": (
                    client_message_id
                ),
                "body": body,
                "message_type": (
                    message_type
                ),
            }
        )

        return {
            "created": False,
            "message": (
                _message_payload(
                    conversation_id=(
                        conversation_id
                    ),
                    sender_id=user_id,
                    recipient_id=victim[
                        "_id"
                    ],
                    client_message_id=(
                        client_message_id
                    ),
                    body=body,
                )
            ),
        }

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        fake_send_chat_message,
    )

    client = _connect(
        app,
        sender_tokens,
    )

    try:
        response = client.emit(
            "message.send",
            {
                "conversation_id": str(
                    conversation_id
                ),
                "client_message_id": (
                    "identity-test-001"
                ),
                "body": "Hello",
                "user_id": str(
                    victim[
                        "_id"
                    ]
                ),
                "sender_id": str(
                    victim[
                        "_id"
                    ]
                ),
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert (
            captured[
                "user_id"
            ]
            == str(
                sender[
                    "_id"
                ]
            )
        )

        assert (
            captured[
                "user_id"
            ]
            != str(
                victim[
                    "_id"
                ]
            )
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_chat_service_error_is_returned_as_safe_ack(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    monkeypatch.setattr(
        realtime_chat,
        "ChatServiceError",
        _MessageServiceError,
    )

    def reject_message(
        **_kwargs,
    ):
        raise _MessageServiceError(
            "CONVERSATION_CLOSED",
            (
                "This conversation "
                "is closed."
            ),
            status_code=409,
        )

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        reject_message,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "message.send",
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "client_message_id": "m-closed",
                "body": "Hello",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CONVERSATION_CLOSED"
        )

        assert (
            response[
                "error"
            ][
                "message"
            ]
            == (
                "This conversation "
                "is closed."
            )
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_revoked_session_cannot_send_after_connection(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    calls = []

    def must_not_send(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        raise AssertionError(
            (
                "Message service must not "
                "run after session revocation."
            )
        )

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        must_not_send,
    )

    client = _connect(
        app,
        tokens,
    )

    assert client.is_connected(
        "/"
    )

    with app.app_context():
        mongo.db.auth_sessions.update_one(
            {
                "session_id": (
                    tokens[
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
                        "realtime-message-test"
                    ),
                },
            },
        )

    try:
        response = client.emit(
            "message.send",
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "client_message_id": (
                    "revoked-message"
                ),
                "body": "Hello",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "INVALID_ACCESS_TOKEN"
        )

        assert calls == []

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_idempotent_retry_does_not_broadcast_second_message_new(
    app,
    monkeypatch,
):
    with app.app_context():
        sender = _create_user()
        recipient = _create_user()

        sender_tokens = _issue(
            sender
        )

        recipient_tokens = _issue(
            recipient
        )

    conversation_id = ObjectId()

    _install_room_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            sender[
                "_id"
            ],
            recipient[
                "_id"
            ],
        },
    )

    message = _message_payload(
        conversation_id=conversation_id,
        sender_id=sender[
            "_id"
        ],
        recipient_id=recipient[
            "_id"
        ],
        client_message_id=(
            "idempotent-message-001"
        ),
    )

    call_count = 0

    def fake_send_chat_message(
        **_kwargs,
    ):
        nonlocal call_count

        call_count += 1

        return {
            "created": (
                call_count == 1
            ),
            "message": message,
        }

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        fake_send_chat_message,
    )

    sender_client = _connect(
        app,
        sender_tokens,
    )

    recipient_client = _connect(
        app,
        recipient_tokens,
    )

    event_payload = {
        "conversation_id": str(
            conversation_id
        ),
        "client_message_id": (
            "idempotent-message-001"
        ),
        "body": (
            "Hello from realtime"
        ),
    }

    try:
        _join(
            sender_client,
            conversation_id,
        )

        _join(
            recipient_client,
            conversation_id,
        )

        first_ack = sender_client.emit(
            "message.send",
            event_payload,
            namespace="/",
            callback=True,
        )

        assert first_ack[
            "ok"
        ] is True

        assert first_ack[
            "created"
        ] is True

        assert len(
            _message_events(
                sender_client
            )
        ) == 1

        assert len(
            _message_events(
                recipient_client
            )
        ) == 1

        second_ack = sender_client.emit(
            "message.send",
            event_payload,
            namespace="/",
            callback=True,
        )

        assert second_ack[
            "ok"
        ] is True

        assert second_ack[
            "created"
        ] is False

        assert second_ack[
            "message"
        ] == message

        assert (
            _message_events(
                sender_client
            )
            == []
        )

        assert (
            _message_events(
                recipient_client
            )
            == []
        )

        assert call_count == 2

    finally:
        if sender_client.is_connected(
            "/"
        ):
            sender_client.disconnect(
                namespace="/"
            )

        if recipient_client.is_connected(
            "/"
        ):
            recipient_client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        {
            "conversation_id": "",
        },
        {
            "conversation_id": "   ",
        },
    ],
)
def test_invalid_message_event_payload_is_rejected_before_service(
    app,
    monkeypatch,
    payload,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    calls = []

    def must_not_send(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        raise AssertionError(
            (
                "Message service must not "
                "run for invalid payload."
            )
        )

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        must_not_send,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "message.send",
            payload,
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CHAT_VALIDATION_ERROR"
        )

        assert calls == []

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        [],
        {},
        {
            "created": True,
        },
        {
            "created": True,
            "message": None,
        },
        {
            "created": True,
            "message": {},
        },
        {
            "created": True,
            "message": {
                "conversation_id": "",
            },
        },
    ],
)
def test_invalid_service_result_is_not_broadcast(
    app,
    monkeypatch,
    invalid_result,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    monkeypatch.setattr(
        realtime_chat,
        "send_chat_message",
        lambda **_kwargs: (
            invalid_result
        ),
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "message.send",
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "client_message_id": (
                    "invalid-state"
                ),
                "body": "Hello",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CHAT_STATE_INVALID"
        )

        assert (
            _message_events(
                client
            )
            == []
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )