"""Cafe verification endpoints: isolated Flask HTTP contract tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from pymongo.errors import OperationFailure

from app.api.v1 import cafe_email_verification_routes as routes
from app.services.cafe_email_verification_service import (
    CafeEmailOtpLimited,
    CafeEmailOtpUnavailable,
    CafeEmailVerificationError,
)
from app.services.cafe_partner_auth_service import CafePartnerAuthError


EMAIL = "owner@example.com"


@pytest.fixture
def context(monkeypatch):
    app = Flask("xuoroni-cafe-email-api-test")
    app.config.update(
        TESTING=True,
        SECRET_KEY="isolated-cafe-email-api-test",
    )
    app.register_blueprint(
        routes.cafe_email_verification_bp,
        url_prefix="/api/v1/cafe-partners/email",
    )

    request_code = Mock(return_value={
        "status": "sent",
        "expires_in": 600,
    })
    verify_code = Mock(return_value={
        "status": "verified",
        "email_verified": True,
    })

    monkeypatch.setattr(
        routes, "request_cafe_email_verification", request_code
    )
    monkeypatch.setattr(
        routes, "verify_cafe_email_code", verify_code
    )

    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        request_code=request_code,
        verify_code=verify_code,
        request_path="/api/v1/cafe-partners/email/request-code",
        verify_path="/api/v1/cafe-partners/email/verify-code",
    )


def test_request_returns_generic_response(context):
    response = context.client.post(
        context.request_path, json={"email": EMAIL}
    )
    assert response.status_code == 202
    assert "If eligible" in response.get_json()["message"]
    assert response.headers["Cache-Control"] == "no-store"
    context.request_code.assert_called_once_with(email=EMAIL)
    context.verify_code.assert_not_called()


def test_verification_does_not_create_login_session(context):
    response = context.client.post(
        context.verify_path,
        json={"email": EMAIL, "code": "123456"},
    )
    assert response.status_code == 200
    assert response.get_json()["email_verified"] is True
    assert response.get_json()["status"] == "pending"
    assert response.headers["Cache-Control"] == "no-store"
    context.verify_code.assert_called_once_with(
        email=EMAIL, code="123456"
    )
    with context.client.session_transaction() as session:
        assert not session


@pytest.mark.parametrize(
    "error,expected",
    [
        (CafeEmailOtpLimited("limited"), 429),
        (CafeEmailVerificationError("not eligible"), 202),
        (CafePartnerAuthError("invalid email"), 202),
        (CafeEmailOtpUnavailable("redis unavailable"), 503),
        (OperationFailure("database unavailable"), 503),
    ],
)
def test_request_error_mapping(context, error, expected):
    context.request_code.side_effect = error
    response = context.client.post(
        context.request_path, json={"email": EMAIL}
    )
    assert response.status_code == expected
    assert "database unavailable" not in response.get_data(
        as_text=True
    )
    context.verify_code.assert_not_called()


@pytest.mark.parametrize(
    "error,expected",
    [
        (CafeEmailOtpLimited("limited"), 429),
        (CafeEmailVerificationError("expired"), 400),
        (CafePartnerAuthError("invalid email"), 400),
        (CafeEmailOtpUnavailable("redis unavailable"), 503),
        (OperationFailure("database unavailable"), 503),
    ],
)
def test_verify_error_mapping(context, error, expected):
    context.verify_code.side_effect = error
    response = context.client.post(
        context.verify_path,
        json={"email": EMAIL, "code": "123456"},
    )
    assert response.status_code == expected
    assert "database unavailable" not in response.get_data(
        as_text=True
    )


@pytest.mark.parametrize("path_name", ["request_path", "verify_path"])
def test_plain_text_rejected(context, path_name):
    response = context.client.post(
        getattr(context, path_name),
        data="plain text",
        content_type="text/plain",
    )
    assert response.status_code == 415
    context.request_code.assert_not_called()
    context.verify_code.assert_not_called()


@pytest.mark.parametrize("path_name", ["request_path", "verify_path"])
def test_invalid_json_rejected(context, path_name):
    response = context.client.post(
        getattr(context, path_name),
        data="{bad json",
        content_type="application/json",
    )
    assert response.status_code == 400
    context.request_code.assert_not_called()
    context.verify_code.assert_not_called()


@pytest.mark.parametrize("path_name", ["request_path", "verify_path"])
def test_oversized_body_rejected(context, path_name):
    response = context.client.post(
        getattr(context, path_name),
        data="x" * 3000,
        content_type="application/json",
    )
    assert response.status_code == 413
    context.request_code.assert_not_called()
    context.verify_code.assert_not_called()


@pytest.mark.parametrize(
    "path_name,payload",
    [
        ("request_path", {}),
        ("request_path", {"email": EMAIL, "is_admin": True}),
        ("verify_path", {"email": EMAIL}),
        ("verify_path", {"email": EMAIL, "code": "123456", "role": "admin"}),
    ],
)
def test_missing_or_extra_fields_rejected(context, path_name, payload):
    response = context.client.post(
        getattr(context, path_name), json=payload
    )
    assert response.status_code == 400
    context.request_code.assert_not_called()
    context.verify_code.assert_not_called()


def test_no_unintended_endpoints(context):
    matching = [
        rule.rule for rule in context.app.url_map.iter_rules()
        if rule.rule.startswith("/api/v1/cafe-partners/email/")
    ]
    assert sorted(matching) == sorted([
        context.request_path,
        context.verify_path,
    ])
