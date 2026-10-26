"""Integration tests for Soultie realtime authentication."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from uuid import uuid4

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.realtime.auth import (
    RealtimeAuthError,
    authenticate_realtime_connection,
)
from app.security.tokens import (
    create_access_token,
)
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_auth"
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
                "xuoroni-realtime-auth-test-"
                "secret-at-least-32-bytes"
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

    document = {
        "_id": user_id,
        "account_status": (
            account_status
        ),
        "created_at": datetime.now(
            timezone.utc
        ),
        "updated_at": datetime.now(
            timezone.utc
        ),
    }

    mongo.db.users.insert_one(
        document
    )

    return mongo.db.users.find_one(
        {
            "_id": user_id,
        }
    )


def _issue_user_session(
    user,
):
    return issue_session(
        user[
            "_id"
        ],
        metadata={
            "source": (
                "realtime-auth-test"
            ),
        },
    )


def test_real_access_token_and_session_authenticate(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
        )

        result = (
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )
        )

        assert (
            result[
                "user_id"
            ]
            == user[
                "_id"
            ]
        )

        assert (
            result[
                "user"
            ][
                "_id"
            ]
            == user[
                "_id"
            ]
        )

        assert (
            result[
                "session_id"
            ]
            == tokens[
                "session_id"
            ]
        )

        assert (
            result[
                "session"
            ][
                "session_id"
            ]
            == tokens[
                "session_id"
            ]
        )

        assert (
            result[
                "token_payload"
            ][
                "sub"
            ]
            == str(
                user[
                    "_id"
                ]
            )
        )

        assert (
            result[
                "token_payload"
            ][
                "sid"
            ]
            == tokens[
                "session_id"
            ]
        )


def test_bearer_auth_payload_authenticates(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
        )

        result = (
            authenticate_realtime_connection(
                {
                    "Authorization": (
                        "Bearer "
                        + tokens[
                            "access_token"
                        ]
                    ),
                }
            )
        )

        assert (
            result[
                "user_id"
            ]
            == user[
                "_id"
            ]
        )


@pytest.mark.parametrize(
    "auth",
    [
        None,
        {},
        [],
        {
            "access_token": "",
        },
        {
            "access_token": "Bearer",
        },
        {
            "access_token": "Bearer ",
        },
        {
            "access_token": 123,
        },
    ],
)
def test_missing_or_empty_auth_is_rejected(
    app,
    auth,
):
    with app.app_context():
        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                auth
            )

        assert (
            exc_info.value.code
            == (
                "REALTIME_"
                "AUTHENTICATION_REQUIRED"
            )
        )


def test_invalid_jwt_is_rejected(
    app,
):
    with app.app_context():
        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        "not-a-valid-jwt"
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_unknown_session_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        token = create_access_token(
            str(
                user[
                    "_id"
                ]
            ),
            session_id=(
                uuid4().hex
            ),
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": token,
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_revoked_session_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
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
                        "test_revoked"
                    ),
                },
            },
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_expired_stored_session_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
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

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_token_user_must_match_session_user(
    app,
):
    with app.app_context():
        first_user = _create_user()
        second_user = _create_user()

        tokens = (
            _issue_user_session(
                first_user
            )
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
                    "user_id": (
                        second_user[
                            "_id"
                        ]
                    ),
                },
            },
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_missing_user_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
        )

        mongo.db.users.delete_one(
            {
                "_id": (
                    user[
                        "_id"
                    ]
                ),
            }
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "ACCOUNT_UNAVAILABLE"
        )


@pytest.mark.parametrize(
    "account_status",
    [
        "paused",
        "suspended",
        "disabled",
    ],
)
def test_inactive_account_is_rejected(
    app,
    account_status,
):
    with app.app_context():
        user = _create_user(
            account_status=(
                account_status
            )
        )

        tokens = (
            _issue_user_session(
                user
            )
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "ACCOUNT_UNAVAILABLE"
        )


def test_session_without_expiry_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
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
                "$unset": {
                    "expires_at": "",
                },
            },
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )


def test_session_with_invalid_user_id_is_rejected(
    app,
):
    with app.app_context():
        user = _create_user()

        tokens = (
            _issue_user_session(
                user
            )
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
                    "user_id": (
                        "not-an-object-id"
                    ),
                },
            },
        )

        with pytest.raises(
            RealtimeAuthError
        ) as exc_info:
            authenticate_realtime_connection(
                {
                    "access_token": (
                        tokens[
                            "access_token"
                        ]
                    ),
                }
            )

        assert (
            exc_info.value.code
            == "INVALID_ACCESS_TOKEN"
        )