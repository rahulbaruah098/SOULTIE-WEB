"""Authenticated HTTP tests for Soultie compatibility."""

from uuid import uuid4

import pytest
from bson import ObjectId

from app import create_app
from app.api.v1.compatibility import routes as compatibility_routes
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.services.compatibility_service import (
    CompatibilityNotFoundError,
)


TEST_DATABASE = (
    "xuoroni_test_compatibility_api"
)


@pytest.fixture()
def app():
    otp_prefix = (
        "xuoroni:test:compatibility-api:"
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
                "xuoroni-compatibility-api-test-"
                "secret-at-least-32-bytes"
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

        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture()
def client(
    app,
):
    return app.test_client()


def _login(
    client,
    *,
    phone="9876543260",
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

    return {
        "payload": payload,
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


def test_compatibility_endpoint_requires_authentication(
    client,
):
    response = client.get(
        (
            "/api/v1/compatibility/"
            + str(ObjectId())
        )
    )

    assert response.status_code == 401

    body = response.get_json()

    assert body[
        "code"
    ] == "AUTHENTICATION_REQUIRED"

    assert body[
        "message"
    ] == (
        "A valid access token is required."
    )

    assert isinstance(
        body.get(
            "request_id"
        ),
        str,
    )

    assert body[
        "request_id"
    ]


def test_authenticated_endpoint_uses_current_user(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    target_id = ObjectId()

    captured = {}

    expected = {
        "algorithm_version": "v1",
        "target_user_id": str(
            target_id
        ),
        "score": 82,
        "band": "strong",
        "has_sufficient_data": True,
        "data_coverage_percent": 80,
        "dimensions": [],
        "reasons": [],
        "shared": {},
    }

    def fake_get_profile_compatibility(
        *,
        requester_id,
        target_user_id,
    ):
        captured[
            "requester_id"
        ] = requester_id

        captured[
            "target_user_id"
        ] = target_user_id

        return expected

    monkeypatch.setattr(
        compatibility_routes,
        "get_profile_compatibility",
        fake_get_profile_compatibility,
    )

    response = client.get(
        (
            "/api/v1/compatibility/"
            + str(target_id)
        ),
        headers=login[
            "headers"
        ],
    )

    assert response.status_code == 200

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "requester_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "target_user_id"
        ]
        == str(target_id)
    )


def test_service_error_is_translated_to_http_response(
    client,
    monkeypatch,
):
    login = _login(
        client,
        phone="9876543261",
    )

    target_id = ObjectId()

    def fake_get_profile_compatibility(
        *,
        requester_id,
        target_user_id,
    ):
        raise CompatibilityNotFoundError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    monkeypatch.setattr(
        compatibility_routes,
        "get_profile_compatibility",
        fake_get_profile_compatibility,
    )

    response = client.get(
        (
            "/api/v1/compatibility/"
            + str(target_id)
        ),
        headers=login[
            "headers"
        ],
    )

    assert response.status_code == 404

    assert response.get_json() == {
        "error": {
            "code": (
                "COMPATIBILITY_NOT_AVAILABLE"
            ),
            "message": (
                "Compatibility is not "
                "available for this profile."
            ),
        }
    }


def test_invalid_target_id_returns_validation_error(
    client,
):
    login = _login(
        client,
        phone="9876543262",
    )

    response = client.get(
        (
            "/api/v1/compatibility/"
            "not-an-object-id"
        ),
        headers=login[
            "headers"
        ],
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body == {
        "error": {
            "code": "INVALID_USER_ID",
            "message": (
                "target_user_id is invalid."
            ),
        }
    }


def test_self_compatibility_is_rejected(
    client,
):
    login = _login(
        client,
        phone="9876543263",
    )

    response = client.get(
        (
            "/api/v1/compatibility/"
            + str(
                login[
                    "user_id"
                ]
            )
        ),
        headers=login[
            "headers"
        ],
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body == {
        "error": {
            "code": (
                "SELF_COMPATIBILITY_NOT_ALLOWED"
            ),
            "message": (
                "A profile cannot calculate "
                "compatibility with itself."
            ),
        }
    }


def test_route_is_get_only(
    client,
):
    response = client.post(
        (
            "/api/v1/compatibility/"
            + str(ObjectId())
        )
    )

    assert response.status_code == 405