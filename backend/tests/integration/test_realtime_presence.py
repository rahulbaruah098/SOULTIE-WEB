"""Integration tests for Xuoroni realtime presence lifecycle."""

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
import app.realtime as realtime_root
from app.realtime.presence import (
    PresenceStateError,
    count_user_presence_sockets,
    is_user_online,
)
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_presence"
)

TEST_PREFIX = (
    "xuoroni:test:realtime-presence"
)

TEST_TTL_SECONDS = 2

TEST_HEARTBEAT_SECONDS = 1


def _clear_presence_keys():
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
                "xuoroni-realtime-presence-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-presence-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "REALTIME_PRESENCE_KEY_PREFIX": (
                TEST_PREFIX
            ),
            "REALTIME_PRESENCE_TTL_SECONDS": (
                TEST_TTL_SECONDS
            ),
            "REALTIME_PRESENCE_HEARTBEAT_SECONDS": (
                TEST_HEARTBEAT_SECONDS
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

        _clear_presence_keys()

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )

        _clear_presence_keys()


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
                "realtime-presence-test"
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


def _presence_keys(
    *,
    user_id,
):
    redis = (
        redis_store.get_client()
    )

    return list(
        redis.scan_iter(
            match=(
                f"{TEST_PREFIX}:"
                f"{user_id}:*"
            )
        )
    )


def test_authenticated_connect_marks_user_online(
    app,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    client = _connect(
        app,
        tokens,
    )

    try:
        assert client.is_connected(
            "/"
        )

        with app.app_context():
            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is True
            )

            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 1
            )

            keys = _presence_keys(
                user_id=user[
                    "_id"
                ]
            )

            assert len(
                keys
            ) == 1

            redis = (
                redis_store.get_client()
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
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )

    with app.app_context():
        assert (
            is_user_online(
                user_id=user[
                    "_id"
                ]
            )
            is False
        )

        assert (
            _presence_keys(
                user_id=user[
                    "_id"
                ]
            )
            == []
        )


def test_heartbeat_refreshes_ttl_and_ignores_forged_user_id(
    app,
):
    with app.app_context():
        user = _create_user()
        victim = _create_user()

        tokens = _issue(
            user
        )

    client = _connect(
        app,
        tokens,
    )

    try:
        assert client.is_connected(
            "/"
        )

        with app.app_context():
            keys = _presence_keys(
                user_id=user[
                    "_id"
                ]
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

        response = client.emit(
            "presence.heartbeat",
            {
                "user_id": str(
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
                user[
                    "_id"
                ]
            )
        )

        assert (
            response[
                "user_id"
            ]
            != str(
                victim[
                    "_id"
                ]
            )
        )

        assert response[
            "online"
        ] is True

        assert response[
            "socket_count"
        ] == 1

        assert response[
            "expires_in"
        ] == TEST_TTL_SECONDS

        assert response[
            "heartbeat_after"
        ] == TEST_HEARTBEAT_SECONDS

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

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_multiple_sockets_keep_user_online_until_final_disconnect(
    app,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    first = _connect(
        app,
        tokens,
    )

    second = _connect(
        app,
        tokens,
    )

    try:
        assert first.is_connected(
            "/"
        )

        assert second.is_connected(
            "/"
        )

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 2
            )

            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is True
            )

        first.disconnect(
            namespace="/"
        )

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 1
            )

            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is True
            )

        second.disconnect(
            namespace="/"
        )

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 0
            )

            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is False
            )

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


def test_revoked_session_heartbeat_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
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
                        "realtime-presence-test"
                    ),
                },
            },
        )

    try:
        response = client.emit(
            "presence.heartbeat",
            {
                "user_id": str(
                    ObjectId()
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

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 1
            )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )

    with app.app_context():
        assert (
            count_user_presence_sockets(
                user_id=user[
                    "_id"
                ]
            )
            == 0
        )


def test_presence_key_expires_without_heartbeat(
    app,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    client = _connect(
        app,
        tokens,
    )

    try:
        assert client.is_connected(
            "/"
        )

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 1
            )

        time.sleep(
            TEST_TTL_SECONDS
            + 0.3
        )

        with app.app_context():
            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is False
            )

            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
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


def test_missing_authentication_does_not_create_presence(
    app,
):
    client = socketio.test_client(
        app,
        namespace="/",
        auth=None,
    )

    assert (
        client.is_connected(
            "/"
        )
        is False
    )

    with app.app_context():
        redis = (
            redis_store.get_client()
        )

        keys = list(
            redis.scan_iter(
                match=f"{TEST_PREFIX}:*"
            )
        )

        assert keys == []


def test_presence_failure_during_connect_does_not_reject_valid_login(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    def presence_unavailable(
        **_kwargs,
    ):
        raise PresenceStateError()

    monkeypatch.setattr(
        realtime_root,
        "mark_socket_online",
        presence_unavailable,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        assert (
            client.is_connected(
                "/"
            )
            is True
        )

        with app.app_context():
            assert (
                _presence_keys(
                    user_id=user[
                        "_id"
                    ]
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


def test_presence_failure_during_heartbeat_returns_safe_error(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    client = _connect(
        app,
        tokens,
    )

    assert client.is_connected(
        "/"
    )

    def presence_unavailable(
        **_kwargs,
    ):
        raise PresenceStateError()

    monkeypatch.setattr(
        realtime_root,
        "refresh_socket_presence",
        presence_unavailable,
    )

    try:
        response = client.emit(
            "presence.heartbeat",
            {},
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

        assert (
            response[
                "error"
            ][
                "message"
            ]
            == (
                "Realtime presence state "
                "is temporarily unavailable."
            )
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_presence_failure_during_disconnect_is_non_fatal(
    app,
    monkeypatch,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    client = _connect(
        app,
        tokens,
    )

    assert client.is_connected(
        "/"
    )

    def presence_unavailable(
        **_kwargs,
    ):
        raise PresenceStateError()

    monkeypatch.setattr(
        realtime_root,
        "remove_socket_presence",
        presence_unavailable,
    )

    client.disconnect(
        namespace="/"
    )

    assert (
        client.is_connected(
            "/"
        )
        is False
    )


def test_reconnect_after_disconnect_creates_fresh_presence(
    app,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    first = _connect(
        app,
        tokens,
    )

    assert first.is_connected(
        "/"
    )

    with app.app_context():
        assert (
            count_user_presence_sockets(
                user_id=user[
                    "_id"
                ]
            )
            == 1
        )

    first.disconnect(
        namespace="/"
    )

    with app.app_context():
        assert (
            count_user_presence_sockets(
                user_id=user[
                    "_id"
                ]
            )
            == 0
        )

    second = _connect(
        app,
        tokens,
    )

    try:
        assert second.is_connected(
            "/"
        )

        with app.app_context():
            assert (
                count_user_presence_sockets(
                    user_id=user[
                        "_id"
                    ]
                )
                == 1
            )

            assert (
                is_user_online(
                    user_id=user[
                        "_id"
                    ]
                )
                is True
            )

    finally:
        if second.is_connected(
            "/"
        ):
            second.disconnect(
                namespace="/"
            )