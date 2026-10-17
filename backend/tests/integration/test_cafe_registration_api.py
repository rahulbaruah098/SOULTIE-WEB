"""Isolated public cafe-registration Flask endpoint tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId
from flask import Flask
from pymongo.errors import OperationFailure

from app.api.v1 import cafe_registration_routes as routes
from app.schemas.venue import VenueValidationError
from app.security.cafe_registration_limit import (
    CafeRegistrationLimitError,
    CafeRegistrationRateExceeded,
)
from app.services.cafe_partner_auth_service import (
    CafePartnerAlreadyRegistered,
    CafePartnerAuthError,
)
from app.services.cafe_registration_service import CafeRegistrationError


PASSWORD = "SafeCafePassword2026!"


def valid_registration():
    return {
        "owner_name": "Test Cafe Owner",
        "owner_email": "owner@example.com",
        "owner_phone": "9876543210",
        "venue_name": "Test Cafe",
        "address": "Market Road",
        "city": "Guwahati",
        "state": "Assam",
        "postal_code": "781001",
        "password": PASSWORD,
    }


@pytest.fixture
def api_context(monkeypatch):
    app = Flask("xuoroni-cafe-registration-api-test")
    app.config.update(
        TESTING=True,
        SECRET_KEY="isolated-cafe-registration-test",
    )
    app.register_blueprint(
        routes.cafe_registration_bp,
        url_prefix="/api/v1/cafe-partners",
    )

    rate_limit = Mock(return_value=None)
    register = Mock(return_value={
        "partner_id": str(ObjectId()),
        "venue_id": str(ObjectId()),
        "status": "pending",
        "email_verified": False,
    })

    monkeypatch.setattr(
        routes,
        "check_cafe_registration_limit",
        rate_limit,
    )
    monkeypatch.setattr(
        routes,
        "register_cafe_with_venue",
        register,
    )

    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        path="/api/v1/cafe-partners/register",
        rate_limit=rate_limit,
        register=register,
    )


def test_registration_succeeds_without_login(api_context):
    ctx = api_context
    payload = valid_registration()

    response = ctx.client.post(ctx.path, json=payload)

    assert response.status_code == 202
    data = response.get_json()
    assert data["status"] == "pending"
    assert data["email_verified"] is False
    assert set(data) == {
        "partner_id", "venue_id", "status",
        "email_verified", "message",
    }
    assert "approval" in data["message"].lower()
    assert PASSWORD not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"

    ctx.rate_limit.assert_called_once_with()
    ctx.register.assert_called_once_with(
        registration={
            key: value
            for key, value in payload.items()
            if key != "password"
        },
        password=PASSWORD,
    )
    with ctx.client.session_transaction() as session:
        assert not session


def test_rate_limit_exceeded_returns_429(api_context):
    ctx = api_context
    ctx.rate_limit.side_effect = CafeRegistrationRateExceeded(
        "Too many requests"
    )

    response = ctx.client.post(
        ctx.path, json=valid_registration()
    )

    assert response.status_code == 429
    assert response.headers["Cache-Control"] == "no-store"
    ctx.register.assert_not_called()


def test_rate_limit_unavailable_returns_503(api_context):
    ctx = api_context
    ctx.rate_limit.side_effect = CafeRegistrationLimitError(
        "Redis unavailable"
    )

    response = ctx.client.post(
        ctx.path, json=valid_registration()
    )

    assert response.status_code == 503
    ctx.register.assert_not_called()
    assert "Redis" not in response.get_data(as_text=True)


def test_non_json_rejected(api_context):
    ctx = api_context

    response = ctx.client.post(
        ctx.path,
        data="plain text",
        content_type="text/plain",
    )

    assert response.status_code == 415
    ctx.rate_limit.assert_called_once()
    ctx.register.assert_not_called()


def test_invalid_json_rejected(api_context):
    ctx = api_context

    response = ctx.client.post(
        ctx.path,
        data="{invalid",
        content_type="application/json",
    )

    assert response.status_code == 400
    ctx.register.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "hello",
        None,
        {},
        {"owner_email": "owner@example.com"},
        {**valid_registration(), "unexpected": "value"},
        {
            **{
                k: v for k, v in valid_registration().items()
                if k != "password"
            }
        },
    ],
)
def test_malformed_payload_rejected(api_context, payload):
    ctx = api_context

    if payload is None:
        # Flask's test client treats json=None as no JSON argument.
        # Send the JSON null literal with the correct content type.
        response = ctx.client.post(
            ctx.path,
            data="null",
            content_type="application/json",
        )
    else:
        response = ctx.client.post(ctx.path, json=payload)

    assert response.status_code == 400
    ctx.register.assert_not_called()


def test_oversized_registration_rejected(api_context):
    ctx = api_context
    payload = valid_registration()
    payload["venue_name"] = "A" * 9000

    response = ctx.client.post(ctx.path, json=payload)

    assert response.status_code == 413
    ctx.rate_limit.assert_not_called()
    ctx.register.assert_not_called()


@pytest.mark.parametrize(
    "error,code",
    [
        (
            CafePartnerAlreadyRegistered("Duplicate email"),
            409,
        ),
        (
            VenueValidationError("Invalid venue"),
            400,
        ),
        (
            CafePartnerAuthError("Invalid password"),
            400,
        ),
        (
            CafeRegistrationError("Registration invalid"),
            400,
        ),
        (
            OperationFailure("Sensitive database failure"),
            503,
        ),
    ],
)
def test_registration_service_errors(api_context, error, code):
    ctx = api_context
    ctx.register.side_effect = error

    response = ctx.client.post(
        ctx.path, json=valid_registration()
    )

    assert response.status_code == code
    if code == 503:
        assert "Sensitive database failure" not in (
            response.get_data(as_text=True)
        )


def test_unregistered_main_app_route_not_required(api_context):
    ctx = api_context
    registered = [
        rule.rule
        for rule in ctx.app.url_map.iter_rules()
    ]
    assert ctx.path in registered
    assert registered.count(ctx.path) == 1


def test_invalid_body_reaches_limiter_first(api_context):
    ctx = api_context

    response = ctx.client.post(
        ctx.path,
        json={"unknown": "value"},
    )

    assert response.status_code == 400
    ctx.rate_limit.assert_called_once_with()
    ctx.register.assert_not_called()
