"""Isolated HTTP security tests for the Soultie cafe partner login."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import cafe_auth_routes as routes
from app.security.cafe_login_limit import (
    CafeLoginLimitUnavailable,
    CafeLoginRateExceeded,
)
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_auth_service import CafePartnerAuthError


PATH = "/cafe-partners/login"
CREDENTIALS = {
    "email": "owner@example.com",
    "password": "correct-password-123",
}


@pytest.fixture
def context(monkeypatch):
    app = Flask("isolated-cafe-auth-api")
    app.config.update(TESTING=True)
    app.register_blueprint(routes.cafe_auth_bp)

    account = {
        "partner_id": "507f1f77bcf86cd799439011",
        "venue_id": "507f191e810c19729de860ea",
        "account_type": "cafe_partner",
        "email": "owner@example.com",
    }
    issued = {
        "access_token": "A" * 64,
        "token_type": "Bearer",
        "expires_in": 43200,
        "account_type": "cafe_partner",
        "partner_id": account["partner_id"],
        "venue_id": account["venue_id"],
    }

    limiter = Mock(return_value=None)
    password = Mock(return_value=account)
    session = Mock(return_value=issued)

    monkeypatch.setattr(
        routes, "check_cafe_login_limit", limiter
    )
    monkeypatch.setattr(
        routes, "authenticate_cafe_partner_password", password
    )
    monkeypatch.setattr(
        routes, "create_cafe_session", session
    )

    return SimpleNamespace(
        client=app.test_client(),
        app=app,
        limiter=limiter,
        password=password,
        session=session,
        account=account,
        issued=issued,
    )


def assert_uncached(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_successful_login_issues_cafe_only_token(context):
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 200
    assert_uncached(response)

    body = response.get_json()
    assert body == context.issued
    assert "refresh_token" not in body
    assert "user_id" not in body
    assert "admin_id" not in body

    context.limiter.assert_called_once_with(
        email="owner@example.com"
    )
    context.password.assert_called_once_with(
        email=CREDENTIALS["email"],
        password=CREDENTIALS["password"],
    )
    context.session.assert_called_once_with(
        partner_id=context.account["partner_id"]
    )


def test_bad_password_rejected_without_session(context):
    context.password.side_effect = CafePartnerAuthError(
        "Invalid email or password."
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 401
    assert_uncached(response)
    assert "access_token" not in response.get_json()
    context.session.assert_not_called()


def test_unapproved_account_rejected(context):
    context.password.side_effect = CafePartnerAuthError(
        "Cafe partner account unavailable."
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 401
    assert_uncached(response)
    context.session.assert_not_called()


def test_live_venue_suspension_rejected(context):
    context.session.side_effect = CafePartnerAccessDenied(
        "Cafe inactive."
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 401
    assert_uncached(response)
    assert "Cafe inactive" not in response.get_data(
        as_text=True
    )


def test_no_account_enumeration_between_auth_failures(context):
    errors = [
        CafePartnerAuthError("Incorrect password"),
        CafePartnerAuthError("Account pending approval"),
    ]
    responses = []
    for error in errors:
        context.password.side_effect = error
        responses.append(
            context.client.post(
                PATH, json=CREDENTIALS
            ).get_json()
        )
    assert responses[0] == responses[1]


def test_ip_or_account_rate_limit_rejected_first(context):
    context.limiter.side_effect = CafeLoginRateExceeded(
        "Too many attempts."
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 429
    assert_uncached(response)
    context.password.assert_not_called()
    context.session.assert_not_called()


def test_redis_unavailable_fails_closed(context):
    context.limiter.side_effect = CafeLoginLimitUnavailable(
        "Redis unavailable"
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 503
    assert_uncached(response)
    context.password.assert_not_called()
    context.session.assert_not_called()
    assert "Redis" not in response.get_data(as_text=True)


def test_mongodb_unavailable_fails_closed(context):
    context.session.side_effect = PyMongoError(
        "Mongo unavailable"
    )
    response = context.client.post(PATH, json=CREDENTIALS)
    assert response.status_code == 503
    assert_uncached(response)
    assert "Mongo" not in response.get_data(as_text=True)
    assert "access_token" not in response.get_json()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "owner@example.com"},
        {"password": "correct-password-123"},
        {
            "email": "owner@example.com",
            "password": "correct-password-123",
            "account_type": "super_admin",
        },
        ["owner@example.com", "password"],
        None,
    ],
)
def test_malformed_json_shapes_fail_before_credentials(
    context, payload
):
    if payload is None:
        response = context.client.post(
            PATH,
            data="null",
            content_type="application/json",
        )
    else:
        response = context.client.post(PATH, json=payload)
    assert response.status_code == 400
    assert_uncached(response)
    context.limiter.assert_called_once()
    context.password.assert_not_called()
    context.session.assert_not_called()


@pytest.mark.parametrize(
    "email,password",
    [
        (None, "valid-password-123"),
        (123, "valid-password-123"),
        ("owner@example.com", None),
        ("owner@example.com", 123),
    ],
)
def test_nonstring_credentials_rejected(
    context, email, password
):
    response = context.client.post(
        PATH, json={"email": email, "password": password}
    )
    assert response.status_code == 401
    assert_uncached(response)
    context.limiter.assert_called_once_with(email=email)
    context.password.assert_not_called()
    context.session.assert_not_called()


def test_invalid_json_charged_before_rejection(context):
    response = context.client.post(
        PATH,
        data="{invalid",
        content_type="application/json",
    )
    assert response.status_code == 400
    assert_uncached(response)
    context.limiter.assert_called_once_with(email=None)
    context.password.assert_not_called()


def test_non_json_request_rejected(context):
    response = context.client.post(
        PATH,
        data="email=owner",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 415
    assert_uncached(response)
    context.password.assert_not_called()
    context.session.assert_not_called()


def test_oversized_json_rejected(context):
    response = context.client.post(
        PATH,
        data='{"email":"' + ("x" * 3000) + '"}',
        content_type="application/json",
    )
    assert response.status_code == 413
    assert_uncached(response)
    context.password.assert_not_called()
    context.session.assert_not_called()


def test_no_password_or_hash_leaks_in_response(context):
    response = context.client.post(PATH, json=CREDENTIALS)
    body = response.get_json()
    serialized = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "password" not in serialized.lower()
    assert CREDENTIALS["password"] not in serialized
    assert "password_hash" not in body
    assert "email" not in body


def test_no_unrelated_dating_or_admin_routes_added(context):
    url_rules = {
        rule.rule
        for rule in context.app.url_map.iter_rules()
    }
    assert PATH in url_rules
    assert "/auth/login" not in url_rules
    assert "/admin/login" not in url_rules


def test_login_is_post_only(context):
    response = context.client.get(PATH)
    assert response.status_code == 405
    context.password.assert_not_called()
    context.session.assert_not_called()
