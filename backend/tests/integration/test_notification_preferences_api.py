"""Authenticated HTTP tests for Soultie Notification Preferences."""

from uuid import uuid4

import pytest
from bson import ObjectId

from app import create_app
from app.api.v1.notifications import (
    routes as notification_routes,
)
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.schemas.notification_preferences import (
    DISABLEABLE_IN_APP_NOTIFICATION_TYPES,
    MANDATORY_IN_APP_NOTIFICATION_TYPES,
)
from app.services.notification_service import (
    NotificationLifecycleError,
)


TEST_DATABASE = (
    "xuoroni_test_notification_preferences_api"
)


@pytest.fixture(scope="module")
def app():
    otp_prefix = (
        "xuoroni:test:notification-preferences-api:"
        + uuid4().hex
        + ":"
    )

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
                "xuoroni-notification-preferences-api-"
                "test-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "EXPOSE_DEV_OTP": True,
            "DEV_OTP_CODE": "654321",
            "OTP_TTL_SECONDS": 60,
            "OTP_RESEND_COOLDOWN_SECONDS": 1,
            "OTP_MAX_ATTEMPTS": 3,
            "OTP_REQUEST_WINDOW_SECONDS": 60,
            "OTP_MAX_REQUESTS_PER_WINDOW": 5,
            "OTP_KEY_PREFIX": otp_prefix,
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

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture()
def client(
    app,
):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_database(
    app,
):
    with app.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

    yield

    with app.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )


def _login(
    client,
    *,
    phone,
):
    request_response = client.post(
        "/api/v1/auth/request-otp",
        json={
            "phone": phone,
        },
    )

    assert (
        request_response.status_code
        == 200
    )

    verify_response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "phone": phone,
            "otp": "654321",
        },
    )

    assert (
        verify_response.status_code
        == 200
    )

    payload = (
        verify_response.get_json()
    )

    user_id = ObjectId(
        payload[
            "user"
        ][
            "id"
        ]
    )

    # Notification Preferences service access is an active-account
    # capability. Keep real authentication/session behavior while
    # explicitly placing the test account in that lifecycle state.
    mongo.db.users.update_one(
        {
            "_id": user_id,
        },
        {
            "$set": {
                "account_status": "active",
            }
        },
    )

    return {
        "user_id": user_id,
        "headers": {
            "Authorization": (
                "Bearer "
                + payload[
                    "access_token"
                ]
            )
        },
    }


def _optional_types():
    values = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    assert len(
        values
    ) >= 2

    return values


@pytest.mark.parametrize(
    (
        "method",
        "payload",
    ),
    [
        (
            "get",
            None,
        ),
        (
            "put",
            {
                "disabled_types": [],
            },
        ),
    ],
)
def test_notification_preferences_endpoints_require_authentication(
    client,
    method,
    payload,
):
    path = (
        "/api/v1/notifications/preferences"
    )

    if method == "get":
        response = client.get(
            path
        )

    else:
        response = client.put(
            path,
            json=payload,
        )

    assert (
        response.status_code
        == 401
    )

    body = response.get_json()

    assert (
        body[
            "code"
        ]
        == "AUTHENTICATION_REQUIRED"
    )

    assert (
        body[
            "message"
        ]
        == (
            "A valid access token is required."
        )
    )

    assert isinstance(
        body.get(
            "request_id"
        ),
        str,
    )


def test_get_notification_preferences_api_creates_default(
    client,
    app,
):
    with app.app_context():
        login = _login(
            client,
            phone="9876543201",
        )

    response = client.get(
        "/api/v1/notifications/preferences",
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert (
        body[
            "preferences"
        ][
            "schema_version"
        ]
        == 1
    )

    assert (
        body[
            "preferences"
        ][
            "in_app"
        ][
            "disabled_types"
        ]
        == []
    )

    assert set(
        body[
            "preferences"
        ][
            "in_app"
        ][
            "mandatory_types"
        ]
    ) == set(
        MANDATORY_IN_APP_NOTIFICATION_TYPES
    )

    assert (
        "user_id"
        not in body[
            "preferences"
        ]
    )

    with app.app_context():
        stored = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": (
                        login[
                            "user_id"
                        ]
                    ),
                }
            )
        )

        assert stored is not None

        assert (
            stored[
                "disabled_types"
            ]
            == []
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": (
                        login[
                            "user_id"
                        ]
                    ),
                }
            )
            == 1
        )


def test_put_notification_preferences_api_persists_owner_state(
    client,
    app,
):
    first_type = (
        _optional_types()[0]
    )

    with app.app_context():
        login = _login(
            client,
            phone="9876543202",
        )

    response = client.put(
        "/api/v1/notifications/preferences",
        headers=login[
            "headers"
        ],
        json={
            "disabled_types": [
                first_type,
                f" {first_type.upper()} ",
                first_type,
            ],
        },
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert (
        body[
            "preferences"
        ][
            "in_app"
        ][
            "disabled_types"
        ]
        == [
            first_type,
        ]
    )

    with app.app_context():
        stored = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": (
                        login[
                            "user_id"
                        ]
                    ),
                }
            )
        )

        assert stored is not None

        assert (
            stored[
                "disabled_types"
            ]
            == [
                first_type,
            ]
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {}
            )
            == 1
        )


def test_notification_preferences_api_rejects_malformed_body(
    client,
    app,
):
    with app.app_context():
        login = _login(
            client,
            phone="9876543203",
        )

    response = client.put(
        "/api/v1/notifications/preferences",
        headers=login[
            "headers"
        ],
        data="not-json",
        content_type="text/plain",
    )

    assert (
        response.status_code
        == 400
    )

    body = response.get_json()

    assert (
        body[
            "error"
        ][
            "code"
        ]
        == "INVALID_NOTIFICATION_PREFERENCES"
    )

    with app.app_context():
        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": (
                        login[
                            "user_id"
                        ]
                    ),
                }
            )
            == 0
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "push_enabled": "false",
        },
        {
            "disabled_types": [
                "security_alert",
            ],
        },
    ],
)
def test_notification_preferences_api_rejects_invalid_preferences(
    client,
    app,
    payload,
):
    phone_by_case = {
        "{}": "9876543204",
        "{'push_enabled': 'false'}": (
            "9876543205"
        ),
        (
            "{'disabled_types': "
            "['security_alert']}"
        ): "9876543206",
    }

    phone = phone_by_case[
        repr(
            payload
        )
    ]

    with app.app_context():
        login = _login(
            client,
            phone=phone,
        )

    response = client.put(
        "/api/v1/notifications/preferences",
        headers=login[
            "headers"
        ],
        json=payload,
    )

    assert (
        response.status_code
        == 400
    )

    body = response.get_json()

    assert (
        body[
            "error"
        ][
            "code"
        ]
        == "INVALID_NOTIFICATION_PREFERENCES"
    )

    with app.app_context():
        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": (
                        login[
                            "user_id"
                        ]
                    ),
                }
            )
            == 0
        )


def test_notification_preferences_api_rejects_owner_override(
    client,
    app,
):
    with app.app_context():
        first = _login(
            client,
            phone="9876543207",
        )

        second = _login(
            client,
            phone="9876543208",
        )

    response = client.put(
        "/api/v1/notifications/preferences",
        headers=first[
            "headers"
        ],
        json={
            "disabled_types": [],
            "user_id": str(
                second[
                    "user_id"
                ]
            ),
        },
    )

    assert (
        response.status_code
        == 400
    )

    body = response.get_json()

    assert (
        body[
            "error"
        ][
            "code"
        ]
        == "INVALID_NOTIFICATION_PREFERENCES"
    )

    with app.app_context():
        assert (
            mongo.db.notification_preferences.count_documents(
                {}
            )
            == 0
        )


def test_notification_preferences_api_keeps_users_isolated(
    client,
    app,
):
    first_type, second_type = (
        _optional_types()[:2]
    )

    with app.app_context():
        first = _login(
            client,
            phone="9876543209",
        )

        second = _login(
            client,
            phone="9876543210",
        )

    first_update = client.put(
        "/api/v1/notifications/preferences",
        headers=first[
            "headers"
        ],
        json={
            "disabled_types": [
                first_type,
            ],
        },
    )

    assert (
        first_update.status_code
        == 200
    )

    second_default = client.get(
        "/api/v1/notifications/preferences",
        headers=second[
            "headers"
        ],
    )

    assert (
        second_default.status_code
        == 200
    )

    assert (
        second_default.get_json()[
            "preferences"
        ][
            "in_app"
        ][
            "disabled_types"
        ]
        == []
    )

    second_update = client.put(
        "/api/v1/notifications/preferences",
        headers=second[
            "headers"
        ],
        json={
            "disabled_types": [
                second_type,
            ],
        },
    )

    assert (
        second_update.status_code
        == 200
    )

    first_after = client.get(
        "/api/v1/notifications/preferences",
        headers=first[
            "headers"
        ],
    )

    second_after = client.get(
        "/api/v1/notifications/preferences",
        headers=second[
            "headers"
        ],
    )

    assert (
        first_after.get_json()[
            "preferences"
        ][
            "in_app"
        ][
            "disabled_types"
        ]
        == [
            first_type,
        ]
    )

    assert (
        second_after.get_json()[
            "preferences"
        ][
            "in_app"
        ][
            "disabled_types"
        ]
        == [
            second_type,
        ]
    )

    with app.app_context():
        first_record = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": (
                        first[
                            "user_id"
                        ]
                    ),
                }
            )
        )

        second_record = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": (
                        second[
                            "user_id"
                        ]
                    ),
                }
            )
        )

        assert (
            first_record[
                "_id"
            ]
            != second_record[
                "_id"
            ]
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {}
            )
            == 2
        )


def test_notification_preferences_api_maps_service_error(
    client,
    app,
    monkeypatch,
):
    with app.app_context():
        login = _login(
            client,
            phone="9876543211",
        )

    def fail_preferences(
        user_id,
    ):
        assert (
            user_id
            == login[
                "user_id"
            ]
        )

        raise NotificationLifecycleError(
            "NOTIFICATION_PREFERENCES_UNAVAILABLE",
            (
                "Notification preferences "
                "are unavailable."
            ),
            status_code=409,
        )

    monkeypatch.setattr(
        notification_routes,
        "get_my_notification_preferences",
        fail_preferences,
    )

    response = client.get(
        "/api/v1/notifications/preferences",
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 409
    )

    body = response.get_json()

    assert body[
        "error"
    ] == {
        "code": (
            "NOTIFICATION_PREFERENCES_UNAVAILABLE"
        ),
        "message": (
            "Notification preferences "
            "are unavailable."
        ),
    }
