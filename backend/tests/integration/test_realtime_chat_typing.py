"""Integration tests for Xuoroni realtime Chat typing state."""

import time
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
    redis_store,
    socketio,
)
import app.realtime.chat as realtime_chat
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_chat_typing"
)

TEST_PREFIX = (
    "xuoroni:test:realtime-typing"
)

TEST_TTL_SECONDS = 2


class _TypingServiceError(
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


def _clear_typing_keys():
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


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "SECRET_KEY": (
                "xuoroni-realtime-typing-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-typing-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "REALTIME_TYPING_KEY_PREFIX": (
                TEST_PREFIX
            ),
            "REALTIME_TYPING_TTL_SECONDS": (
                TEST_TTL_SECONDS
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

        _clear_typing_keys()

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )

        _clear_typing_keys()


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
                "realtime-chat-typing-test"
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
    conversation_id,
    allowed_user_ids,
    calls=None,
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

    monkeypatch.setattr(
        realtime_chat,
        "ChatServiceError",
        _TypingServiceError,
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
            not in allowed
            or str(
                conversation_id
            )
            != expected_conversation_id
        ):
            raise _TypingServiceError(
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


def _events(
    client,
    event_name,
):
    return [
        packet
        for packet
        in client.get_received(
            namespace="/"
        )
        if packet.get(
            "name"
        )
        == event_name
    ]


def _typing_keys(
    *,
    conversation_id,
    user_id,
):
    redis = (
        redis_store.get_client()
    )

    return list(
        redis.scan_iter(
            match=(
                f"{TEST_PREFIX}:"
                f"{conversation_id}:"
                f"{user_id}:*"
            )
        )
    )


def test_typing_start_creates_ttl_and_broadcasts_only_to_room(
    app,
    monkeypatch,
):
    with app.app_context():
        actor = _create_user()
        peer = _create_user()
        outsider = _create_user()

        actor_tokens = _issue(
            actor
        )

        peer_tokens = _issue(
            peer
        )

        outsider_tokens = _issue(
            outsider
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
            peer[
                "_id"
            ],
        },
    )

    actor_client = _connect(
        app,
        actor_tokens,
    )

    peer_client = _connect(
        app,
        peer_tokens,
    )

    outsider_client = _connect(
        app,
        outsider_tokens,
    )

    try:
        _join(
            actor_client,
            conversation_id,
        )

        _join(
            peer_client,
            conversation_id,
        )

        response = actor_client.emit(
            "typing.start",
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

        assert response[
            "typing"
        ] is True

        assert response[
            "expires_in"
        ] == TEST_TTL_SECONDS

        assert (
            response[
                "user_id"
            ]
            == str(
                actor[
                    "_id"
                ]
            )
        )

        assert (
            _events(
                actor_client,
                "typing.start",
            )
            == []
        )

        peer_events = _events(
            peer_client,
            "typing.start",
        )

        assert len(
            peer_events
        ) == 1

        assert (
            peer_events[
                0
            ][
                "args"
            ][
                0
            ]
            == {
                "conversation_id": str(
                    conversation_id
                ),
                "user_id": str(
                    actor[
                        "_id"
                    ]
                ),
                "typing": True,
                "expires_in": (
                    TEST_TTL_SECONDS
                ),
            }
        )

        assert (
            _events(
                outsider_client,
                "typing.start",
            )
            == []
        )

        with app.app_context():
            keys = _typing_keys(
                conversation_id=(
                    conversation_id
                ),
                user_id=actor[
                    "_id"
                ],
            )

            assert len(
                keys
            ) == 1

            redis = (
                redis_store.get_client()
            )

            assert (
                redis.get(
                    keys[
                        0
                    ]
                )
                == "1"
            )

            ttl = redis.ttl(
                keys[
                    0
                ]
            )

            assert (
                1
                <= ttl
                <= TEST_TTL_SECONDS
            )

    finally:
        for client in (
            actor_client,
            peer_client,
            outsider_client,
        ):
            if client.is_connected(
                "/"
            ):
                client.disconnect(
                    namespace="/"
                )


def test_typing_start_refreshes_ttl_and_stale_key_expires(
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
        conversation_id=conversation_id,
        allowed_user_ids={
            user[
                "_id"
            ],
        },
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        first = client.emit(
            "typing.start",
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

        with app.app_context():
            keys = _typing_keys(
                conversation_id=(
                    conversation_id
                ),
                user_id=user[
                    "_id"
                ],
            )

            assert len(
                keys
            ) == 1

            key = keys[
                0
            ]

        time.sleep(
            1.1
        )

        with app.app_context():
            redis = (
                redis_store.get_client()
            )

            decayed_ttl = (
                redis.ttl(
                    key
                )
            )

            assert (
                0
                <= decayed_ttl
                < TEST_TTL_SECONDS
            )

        second = client.emit(
            "typing.start",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert second[
            "ok"
        ] is True

        with app.app_context():
            redis = (
                redis_store.get_client()
            )

            refreshed_ttl = (
                redis.ttl(
                    key
                )
            )

            assert (
                1
                <= refreshed_ttl
                <= TEST_TTL_SECONDS
            )

            assert (
                refreshed_ttl
                > decayed_ttl
            )

        time.sleep(
            TEST_TTL_SECONDS
            + 0.3
        )

        with app.app_context():
            redis = (
                redis_store.get_client()
            )

            assert (
                redis.exists(
                    key
                )
                == 0
            )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_client_cannot_forge_typing_identity(
    app,
    monkeypatch,
):
    with app.app_context():
        actor = _create_user()
        victim = _create_user()

        tokens = _issue(
            actor
        )

    conversation_id = ObjectId()
    calls = []

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
        },
        calls=calls,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "typing.start",
            {
                "conversation_id": str(
                    conversation_id
                ),
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
            response[
                "user_id"
            ]
            == str(
                actor[
                    "_id"
                ]
            )
        )

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
                actor[
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
                victim[
                    "_id"
                ]
            )
        )

        with app.app_context():
            actor_keys = (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
                )
            )

            victim_keys = (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=victim[
                        "_id"
                    ],
                )
            )

            assert len(
                actor_keys
            ) == 1

            assert victim_keys == []

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_revoked_session_cannot_create_typing_state(
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
        conversation_id=conversation_id,
        allowed_user_ids={
            user[
                "_id"
            ],
        },
        calls=calls,
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
                        "realtime-typing-test"
                    ),
                },
            },
        )

    try:
        response = client.emit(
            "typing.start",
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

        with app.app_context():
            assert (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=user[
                        "_id"
                    ],
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


def test_typing_stop_is_idempotent_and_does_not_duplicate_broadcast(
    app,
    monkeypatch,
):
    with app.app_context():
        actor = _create_user()
        peer = _create_user()

        actor_tokens = _issue(
            actor
        )

        peer_tokens = _issue(
            peer
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
            peer[
                "_id"
            ],
        },
    )

    actor_client = _connect(
        app,
        actor_tokens,
    )

    peer_client = _connect(
        app,
        peer_tokens,
    )

    try:
        _join(
            actor_client,
            conversation_id,
        )

        _join(
            peer_client,
            conversation_id,
        )

        started = actor_client.emit(
            "typing.start",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert started[
            "ok"
        ] is True

        # Consume the start notification before testing stop.
        assert len(
            _events(
                peer_client,
                "typing.start",
            )
        ) == 1

        first_stop = actor_client.emit(
            "typing.stop",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert first_stop[
            "ok"
        ] is True

        assert first_stop[
            "changed"
        ] is True

        assert first_stop[
            "still_typing"
        ] is False

        first_events = _events(
            peer_client,
            "typing.stop",
        )

        assert len(
            first_events
        ) == 1

        assert (
            first_events[
                0
            ][
                "args"
            ][
                0
            ][
                "typing"
            ]
            is False
        )

        with app.app_context():
            assert (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
                )
                == []
            )

        second_stop = actor_client.emit(
            "typing.stop",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert second_stop[
            "ok"
        ] is True

        assert second_stop[
            "changed"
        ] is False

        assert second_stop[
            "still_typing"
        ] is False

        assert (
            _events(
                peer_client,
                "typing.stop",
            )
            == []
        )

    finally:
        if actor_client.is_connected(
            "/"
        ):
            actor_client.disconnect(
                namespace="/"
            )

        if peer_client.is_connected(
            "/"
        ):
            peer_client.disconnect(
                namespace="/"
            )


def test_multi_socket_stop_does_not_false_broadcast_until_last_socket(
    app,
    monkeypatch,
):
    with app.app_context():
        actor = _create_user()
        peer = _create_user()

        actor_tokens = _issue(
            actor
        )

        peer_tokens = _issue(
            peer
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
            peer[
                "_id"
            ],
        },
    )

    first_actor = _connect(
        app,
        actor_tokens,
    )

    second_actor = _connect(
        app,
        actor_tokens,
    )

    peer_client = _connect(
        app,
        peer_tokens,
    )

    try:
        _join(
            first_actor,
            conversation_id,
        )

        _join(
            second_actor,
            conversation_id,
        )

        _join(
            peer_client,
            conversation_id,
        )

        first_start = first_actor.emit(
            "typing.start",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        second_start = second_actor.emit(
            "typing.start",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert first_start[
            "ok"
        ] is True

        assert second_start[
            "ok"
        ] is True

        with app.app_context():
            assert len(
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
                )
            ) == 2

        # Remove all start notifications before checking stops.
        first_actor.get_received(
            namespace="/"
        )

        second_actor.get_received(
            namespace="/"
        )

        peer_client.get_received(
            namespace="/"
        )

        first_stop = first_actor.emit(
            "typing.stop",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert first_stop[
            "ok"
        ] is True

        assert first_stop[
            "changed"
        ] is True

        assert first_stop[
            "still_typing"
        ] is True

        assert (
            _events(
                peer_client,
                "typing.stop",
            )
            == []
        )

        with app.app_context():
            assert len(
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
                )
            ) == 1

        second_stop = second_actor.emit(
            "typing.stop",
            {
                "conversation_id": str(
                    conversation_id
                ),
            },
            namespace="/",
            callback=True,
        )

        assert second_stop[
            "ok"
        ] is True

        assert second_stop[
            "changed"
        ] is True

        assert second_stop[
            "still_typing"
        ] is False

        peer_stop_events = _events(
            peer_client,
            "typing.stop",
        )

        assert len(
            peer_stop_events
        ) == 1

        assert (
            peer_stop_events[
                0
            ][
                "args"
            ][
                0
            ][
                "user_id"
            ]
            == str(
                actor[
                    "_id"
                ]
            )
        )

        with app.app_context():
            assert (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
                )
                == []
            )

    finally:
        for client in (
            first_actor,
            second_actor,
            peer_client,
        ):
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
    ],
)
def test_invalid_typing_start_payload_is_rejected_before_authorization(
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

    def must_not_authorize(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        raise AssertionError(
            (
                "Conversation authorization "
                "must not run for invalid "
                "typing payload."
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
            "typing.start",
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


def test_non_member_cannot_set_typing_state(
    app,
    monkeypatch,
):
    with app.app_context():
        actor = _create_user()
        other = _create_user()

        tokens = _issue(
            actor
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            other[
                "_id"
            ],
        },
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            "typing.start",
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

        with app.app_context():
            assert (
                _typing_keys(
                    conversation_id=(
                        conversation_id
                    ),
                    user_id=actor[
                        "_id"
                    ],
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


@pytest.mark.parametrize(
    "event_name",
    [
        "typing.start",
        "typing.stop",
    ],
)
def test_redis_failure_returns_safe_realtime_state_error(
    app,
    monkeypatch,
    event_name,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    conversation_id = ObjectId()

    _install_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            user[
                "_id"
            ],
        },
    )

    def redis_unavailable():
        raise RuntimeError(
            "Redis unavailable"
        )

    monkeypatch.setattr(
        realtime_chat.redis_store,
        "get_client",
        redis_unavailable,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            event_name,
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
            == "REALTIME_STATE_UNAVAILABLE"
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )