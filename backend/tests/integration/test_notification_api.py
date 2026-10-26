"""Integration tests for the Soultie Notification REST API."""

import pytest
from flask import Flask

from app.api.v1 import api_v1_bp
from app.api.v1.notifications import routes as notification_routes
from app.security import authentication as authentication_module
from app.services.notification_service import (
    NotificationValidationError,
)


TEST_USER_ID = "notification-api-test-user"
TEST_ACCESS_TOKEN = "notification-api-test-token"


@pytest.fixture()
def app():
    flask_app = Flask(
        "notification_api_integration_tests"
    )

    flask_app.config.update(
        TESTING=True,
    )

    flask_app.register_blueprint(
        api_v1_bp
    )

    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def authenticated_request(
    monkeypatch,
):
    def fake_authenticate_access_token(
        token,
    ):
        assert (
            token
            == TEST_ACCESS_TOKEN
        )

        return {
            "payload": {
                "sub": TEST_USER_ID,
                "sid": "notification-api-session",
            },
            "session": {
                "_id": "notification-api-session",
                "user_id": TEST_USER_ID,
                "revoked_at": None,
            },
            "user": {
                "_id": TEST_USER_ID,
                "account_status": "active",
            },
        }

    monkeypatch.setattr(
        authentication_module,
        "authenticate_access_token",
        fake_authenticate_access_token,
    )

    return {
        "user_id": TEST_USER_ID,
        "headers": {
            "Authorization": (
                f"Bearer {TEST_ACCESS_TOKEN}"
            ),
        },
    }


def test_registered_notification_api_route_map(
    app,
):
    routes = {
        (
            rule.rule,
            frozenset(
                method
                for method in rule.methods
                if method
                not in {
                    "HEAD",
                    "OPTIONS",
                }
            ),
        )
        for rule in app.url_map.iter_rules()
        if (
            rule.rule
            == "/api/v1/notifications"
            or rule.rule.startswith(
                "/api/v1/notifications/"
            )
        )
    }

    assert routes == {
        (
            "/api/v1/notifications",
            frozenset(
                {
                    "GET",
                }
            ),
        ),
        (
            "/api/v1/notifications/preferences",
            frozenset(
                {
                    "GET",
                }
            ),
        ),
        (
            "/api/v1/notifications/preferences",
            frozenset(
                {
                    "PUT",
                }
            ),
        ),
        (
            "/api/v1/notifications/unread-count",
            frozenset(
                {
                    "GET",
                }
            ),
        ),
        (
            "/api/v1/notifications/<notification_id>/read",
            frozenset(
                {
                    "POST",
                }
            ),
        ),
        (
            "/api/v1/notifications/read-all",
            frozenset(
                {
                    "POST",
                }
            ),
        ),
    }


@pytest.mark.parametrize(
    (
        "method",
        "path",
    ),
    [
        (
            "GET",
            "/api/v1/notifications",
        ),
        (
            "GET",
            "/api/v1/notifications/unread-count",
        ),
        (
            "POST",
            (
                "/api/v1/notifications/"
                "507f1f77bcf86cd799439011/read"
            ),
        ),
        (
            "POST",
            "/api/v1/notifications/read-all",
        ),
    ],
)
def test_notifications_require_authentication(
    client,
    method,
    path,
):
    response = client.open(
        path,
        method=method,
    )

    assert (
        response.status_code
        == 401
    )

    payload = response.get_json()

    assert (
        payload[
            "code"
        ]
        == "AUTHENTICATION_REQUIRED"
    )

    assert (
        payload[
            "message"
        ]
        == "A valid access token is required."
    )


def test_list_notifications_api_defaults(
    client,
    monkeypatch,
    authenticated_request,
):
    captured = {}

    expected = {
        "notifications": [
            {
                "id": "notification-1",
                "type": "security",
                "read": False,
            },
        ],
        "next_cursor": None,
    }

    def fake_list_my_notifications(
        user_id,
        *,
        limit,
        cursor,
        unread_only,
    ):
        captured.update(
            {
                "user_id": user_id,
                "limit": limit,
                "cursor": cursor,
                "unread_only": unread_only,
            }
        )

        return expected

    monkeypatch.setattr(
        notification_routes,
        "list_my_notifications",
        fake_list_my_notifications,
    )

    response = client.get(
        "/api/v1/notifications",
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert captured == {
        "user_id": authenticated_request[
            "user_id"
        ],
        "limit": "20",
        "cursor": None,
        "unread_only": False,
    }


def test_list_notifications_api_query_parameters(
    client,
    monkeypatch,
    authenticated_request,
):
    captured = {}

    expected = {
        "notifications": [],
        "next_cursor": "next-page",
    }

    def fake_list_my_notifications(
        user_id,
        *,
        limit,
        cursor,
        unread_only,
    ):
        captured.update(
            {
                "user_id": user_id,
                "limit": limit,
                "cursor": cursor,
                "unread_only": unread_only,
            }
        )

        return expected

    monkeypatch.setattr(
        notification_routes,
        "list_my_notifications",
        fake_list_my_notifications,
    )

    response = client.get(
        (
            "/api/v1/notifications"
            "?limit=7"
            "&cursor=current-page"
            "&unread_only=true"
        ),
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert captured == {
        "user_id": authenticated_request[
            "user_id"
        ],
        "limit": "7",
        "cursor": "current-page",
        "unread_only": True,
    }


def test_list_notifications_rejects_invalid_unread_only(
    client,
    monkeypatch,
    authenticated_request,
):
    def fail_if_service_called(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Notification service must not "
            "run for an invalid query."
        )

    monkeypatch.setattr(
        notification_routes,
        "list_my_notifications",
        fail_if_service_called,
    )

    response = client.get(
        (
            "/api/v1/notifications"
            "?unread_only=1"
        ),
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 400
    )

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "INVALID_NOTIFICATION_LIST_QUERY"
    )

    assert (
        payload[
            "error"
        ][
            "message"
        ]
        == "unread_only must be true or false."
    )


def test_notification_unread_count_api(
    client,
    monkeypatch,
    authenticated_request,
):
    captured = {}

    def fake_get_unread_count(
        user_id,
    ):
        captured[
            "user_id"
        ] = user_id

        return {
            "unread_count": 4,
        }

    monkeypatch.setattr(
        notification_routes,
        "get_my_unread_notification_count",
        fake_get_unread_count,
    )

    response = client.get(
        "/api/v1/notifications/unread-count",
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert response.get_json() == {
        "unread_count": 4,
    }

    assert (
        captured[
            "user_id"
        ]
        == authenticated_request[
            "user_id"
        ]
    )


def test_mark_notification_read_api(
    client,
    monkeypatch,
    authenticated_request,
):
    captured = {}

    notification_id = (
        "507f1f77bcf86cd799439011"
    )

    expected = {
        "notification": {
            "id": notification_id,
            "read": True,
        },
    }

    def fake_mark_read(
        *,
        user_id,
        notification_id,
    ):
        captured.update(
            {
                "user_id": user_id,
                "notification_id": (
                    notification_id
                ),
            }
        )

        return expected

    monkeypatch.setattr(
        notification_routes,
        "mark_my_notification_read",
        fake_mark_read,
    )

    response = client.post(
        (
            "/api/v1/notifications/"
            f"{notification_id}/read"
        ),
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert captured == {
        "user_id": authenticated_request[
            "user_id"
        ],
        "notification_id": notification_id,
    }


def test_mark_all_notifications_read_api(
    client,
    monkeypatch,
    authenticated_request,
):
    captured = {}

    expected = {
        "modified_count": 3,
    }

    def fake_mark_all_read(
        user_id,
    ):
        captured[
            "user_id"
        ] = user_id

        return expected

    monkeypatch.setattr(
        notification_routes,
        "mark_all_my_notifications_read",
        fake_mark_all_read,
    )

    response = client.post(
        "/api/v1/notifications/read-all",
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == authenticated_request[
            "user_id"
        ]
    )


def test_notification_service_error_maps_to_http_response(
    client,
    monkeypatch,
    authenticated_request,
):
    def fail_unread_count(
        user_id,
    ):
        assert (
            user_id
            == authenticated_request[
                "user_id"
            ]
        )

        raise NotificationValidationError(
            "NOTIFICATION_API_TEST_ERROR",
            "Notification request rejected.",
            status_code=400,
        )

    monkeypatch.setattr(
        notification_routes,
        "get_my_unread_notification_count",
        fail_unread_count,
    )

    response = client.get(
        "/api/v1/notifications/unread-count",
        headers=authenticated_request[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 400
    )

    assert response.get_json() == {
        "error": {
            "code": (
                "NOTIFICATION_API_TEST_ERROR"
            ),
            "message": (
                "Notification request rejected."
            ),
        },
    }
