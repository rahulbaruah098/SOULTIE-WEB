"""Socket.IO connection integration tests for Soultie realtime."""

from datetime import (
    datetime,
    timedelta,
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
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_connection"
)


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "SECRET_KEY": (
                "xuoroni-realtime-connection-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-connection-"
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


def _create_user(
    *,
    account_status="active",
):
    user_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    document = {
        "_id": user_id,
        "account_status": (
            account_status
        ),
        "created_at": now,
        "updated_at": now,
    }

    mongo.db.users.insert_one(
        document
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
                "realtime-connection-test"
            ),
        },
    )


def _connect(
    app,
    *,
    auth=None,
):
    return socketio.test_client(
        app,
        namespace="/",
        auth=auth,
    )


def test_realtime_handlers_are_registered_by_normal_app_factory(
    app,
):
    assert socketio.server is not None

    handlers = (
        socketio.server.handlers.get(
            "/",
            {}
        )
    )

    assert (
        "connect"
        in handlers
    )

    assert (
        "disconnect"
        in handlers
    )

    assert callable(
        handlers[
            "connect"
        ]
    )

    assert callable(
        handlers[
            "disconnect"
        ]
    )


def test_valid_access_token_connects(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

    client = _connect(
        app,
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )

    try:
        assert (
            client.is_connected(
                namespace="/"
            )
            is True
        )

    finally:
        if client.is_connected(
            namespace="/"
        ):
            client.disconnect(
                namespace="/"
            )


def test_bearer_authorization_connects(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

    client = _connect(
        app,
        auth={
            "Authorization": (
                "Bearer "
                + tokens[
                    "access_token"
                ]
            ),
        },
    )

    try:
        assert (
            client.is_connected(
                namespace="/"
            )
            is True
        )

    finally:
        if client.is_connected(
            namespace="/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    "auth",
    [
        None,
        {},
        {
            "access_token": "",
        },
        {
            "access_token": "Bearer",
        },
        {
            "access_token": "Bearer ",
        },
    ],
)
def test_missing_authentication_is_refused(
    app,
    auth,
):
    client = _connect(
        app,
        auth=auth,
    )

    assert (
        client.is_connected(
            namespace="/"
        )
        is False
    )


def test_invalid_access_token_is_refused(
    app,
):
    client = _connect(
        app,
        auth={
            "access_token": (
                "definitely-not-a-valid-jwt"
            ),
        },
    )

    assert (
        client.is_connected(
            namespace="/"
        )
        is False
    )


def test_revoked_session_is_refused(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

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
                        "realtime_connection_test"
                    ),
                },
            },
        )

    client = _connect(
        app,
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )

    assert (
        client.is_connected(
            namespace="/"
        )
        is False
    )


def test_expired_stored_session_is_refused(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

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
                    "expires_at": (
                        datetime.now(
                            timezone.utc
                        )
                        - timedelta(
                            minutes=1
                        )
                    ),
                },
            },
        )

    client = _connect(
        app,
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )

    assert (
        client.is_connected(
            namespace="/"
        )
        is False
    )


@pytest.mark.parametrize(
    "account_status",
    [
        "paused",
        "suspended",
        "disabled",
    ],
)
def test_inactive_account_is_refused(
    app,
    account_status,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

        mongo.db.users.update_one(
            {
                "_id": (
                    user[
                        "_id"
                    ]
                ),
            },
            {
                "$set": {
                    "account_status": (
                        account_status
                    ),
                },
            },
        )

    client = _connect(
        app,
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )

    assert (
        client.is_connected(
            namespace="/"
        )
        is False
    )


def test_same_user_can_hold_multiple_socket_connections(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

    auth = {
        "access_token": (
            tokens[
                "access_token"
            ]
        ),
    }

    first = _connect(
        app,
        auth=auth,
    )

    second = _connect(
        app,
        auth=auth,
    )

    try:
        assert (
            first.is_connected(
                namespace="/"
            )
            is True
        )

        assert (
            second.is_connected(
                namespace="/"
            )
            is True
        )

    finally:
        if first.is_connected(
            namespace="/"
        ):
            first.disconnect(
                namespace="/"
            )

        if second.is_connected(
            namespace="/"
        ):
            second.disconnect(
                namespace="/"
            )


def test_disconnect_does_not_revoke_login_session(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = _issue(
            user
        )

    auth = {
        "access_token": (
            tokens[
                "access_token"
            ]
        ),
    }

    first = _connect(
        app,
        auth=auth,
    )

    assert (
        first.is_connected(
            namespace="/"
        )
        is True
    )

    first.disconnect(
        namespace="/"
    )

    assert (
        first.is_connected(
            namespace="/"
        )
        is False
    )

    second = _connect(
        app,
        auth=auth,
    )

    try:
        assert (
            second.is_connected(
                namespace="/"
            )
            is True
        )

    finally:
        if second.is_connected(
            namespace="/"
        ):
            second.disconnect(
                namespace="/"
            )