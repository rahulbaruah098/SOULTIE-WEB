"""Integration tests for Xuoroni realtime Chat room authorization."""

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
from app.realtime.chat import (
    conversation_room_name,
)
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_chat_rooms"
)


class _RoomServiceError(
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
                "xuoroni-realtime-room-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-room-"
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
                "realtime-chat-room-test"
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
            ObjectId()
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


def _install_authorizer(
    monkeypatch,
    *,
    allowed_user_id,
    conversation_id,
    calls=None,
):
    monkeypatch.setattr(
        realtime_chat,
        "ChatServiceError",
        _RoomServiceError,
    )

    expected_conversation_id = str(
        conversation_id
    )

    expected_user_id = str(
        allowed_user_id
    )

    def fake_get_my_conversation(
        *,
        user_id,
        conversation_id,
    ):
        if calls is not None:
            calls.append(
                {
                    "user_id": str(
                        user_id
                    ),
                    "conversation_id": str(
                        conversation_id
                    ),
                }
            )

        if (
            str(
                user_id
            )
            != expected_user_id
            or str(
                conversation_id
            )
            != expected_conversation_id
        ):
            raise _RoomServiceError(
                "CONVERSATION_NOT_FOUND",
                (
                    "Conversation was "
                    "not found."
                ),
                status_code=404,
            )

        return _conversation_payload(
            expected_conversation_id
        )

    monkeypatch.setattr(
        realtime_chat,
        "get_my_conversation",
        fake_get_my_conversation,
    )


def _client_is_in_room(
    client,
    room,
):
    participants = list(
        socketio.server.manager.get_participants(
            "/",
            room,
        )
    )

    return any(
        eio_sid
        == client.eio_sid
        for (
            _socket_sid,
            eio_sid,
        )
        in participants
    )


def _room_eio_sids(
    room,
):
    return {
        eio_sid
        for (
            _socket_sid,
            eio_sid,
        )
        in socketio.server.manager.get_participants(
            "/",
            room,
        )
    }


def test_authorized_member_can_join_room(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        allowed_user_id=user[
            "_id"
        ],
        conversation_id=conversation_id,
    )

    client = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
    )

    try:
        assert client.is_connected(
            "/"
        )

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

        assert (
            response[
                "conversation"
            ][
                "id"
            ]
            == str(
                conversation_id
            )
        )

        assert _client_is_in_room(
            client,
            room,
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_non_member_cannot_join_room(
    app,
    monkeypatch,
):
    with app.app_context():
        current_user = _create_user()
        other_user = _create_user()

        tokens = _issue(
            current_user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        allowed_user_id=other_user[
            "_id"
        ],
        conversation_id=conversation_id,
    )

    client = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
    )

    try:
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
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CONVERSATION_NOT_FOUND"
        )

        assert (
            _client_is_in_room(
                client,
                room,
            )
            is False
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
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
        {
            "conversation_id": 123,
        },
    ],
)
def test_invalid_join_payload_is_rejected(
    app,
    monkeypatch,
    payload,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    def must_not_authorize(
        **_kwargs,
    ):
        raise AssertionError(
            (
                "Conversation authorization "
                "must not run for an invalid "
                "event payload."
            )
        )

    monkeypatch.setattr(
        realtime_chat,
        "get_my_conversation",
        must_not_authorize,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "chat:join",
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

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_chat_service_error_is_forwarded_safely(
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
        _RoomServiceError,
    )

    def reject_conversation(
        **_kwargs,
    ):
        raise _RoomServiceError(
            "INVALID_ID",
            (
                "conversation_id "
                "is invalid."
            ),
            status_code=400,
        )

    monkeypatch.setattr(
        realtime_chat,
        "get_my_conversation",
        reject_conversation,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "chat:join",
            {
                "conversation_id": (
                    "bad-conversation-id"
                ),
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
            == "INVALID_ID"
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_authorized_leave_removes_socket_from_room(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        allowed_user_id=user[
            "_id"
        ],
        conversation_id=conversation_id,
    )

    client = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
    )

    try:
        joined = client.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert joined[
            "ok"
        ] is True

        assert _client_is_in_room(
            client,
            room,
        )

        left = client.emit(
            "chat:leave",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert left[
            "ok"
        ] is True

        assert (
            left[
                "conversation_id"
            ]
            == str(
                conversation_id
            )
        )

        assert (
            _client_is_in_room(
                client,
                room,
            )
            is False
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_revoked_session_blocks_room_event_after_connection(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()
    calls = []

    _install_authorizer(
        monkeypatch,
        allowed_user_id=user[
            "_id"
        ],
        conversation_id=conversation_id,
        calls=calls,
    )

    client = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
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
                        "realtime-room-test"
                    ),
                },
            },
        )

    try:
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

        assert (
            _client_is_in_room(
                client,
                room,
            )
            is False
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_client_supplied_user_id_cannot_impersonate_another_user(
    app,
    monkeypatch,
):
    with app.app_context():
        current_user = _create_user()
        other_user = _create_user()

        tokens = _issue(
            current_user
        )

    conversation_id = ObjectId()
    calls = []

    _install_authorizer(
        monkeypatch,
        allowed_user_id=current_user[
            "_id"
        ],
        conversation_id=conversation_id,
        calls=calls,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
                "user_id": str(
                    other_user[
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
                current_user[
                    "_id"
                ]
            )
        )

        assert (
            calls[
                0
            ][
                "user_id"
            ]
            != str(
                other_user[
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


def test_repeated_join_is_idempotent_for_same_socket(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        allowed_user_id=user[
            "_id"
        ],
        conversation_id=conversation_id,
    )

    client = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
    )

    try:
        first = client.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        second = client.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert first[
            "ok"
        ] is True

        assert second[
            "ok"
        ] is True

        participants = (
            _room_eio_sids(
                room
            )
        )

        assert (
            client.eio_sid
            in participants
        )

        assert len(
            participants
        ) == 1

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_multiple_sockets_for_same_user_can_join_room(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        allowed_user_id=user[
            "_id"
        ],
        conversation_id=conversation_id,
    )

    first = _connect(
        app,
        tokens,
    )

    second = _connect(
        app,
        tokens,
    )

    room = conversation_room_name(
        conversation_id
    )

    try:
        first_response = first.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        second_response = second.emit(
            "chat:join",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert first_response[
            "ok"
        ] is True

        assert second_response[
            "ok"
        ] is True

        participants = (
            _room_eio_sids(
                room
            )
        )

        assert (
            first.eio_sid
            in participants
        )

        assert (
            second.eio_sid
            in participants
        )

        assert len(
            participants
        ) == 2

    finally:
        if first.is_connected(
            "/"
        ):
            first.disconnect(
                namespace="/"
            )

        if second.is_connected(
            "/"
        ):
            second.disconnect(
                namespace="/"
            )