"""HTTP security tests for cafe-partner logout-all sessions."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import cafe_auth_routes as routes
from app.services.cafe_partner_session_service import CafeSessionInvalid


PATH = "/cafe-partners/logout-all"
TOKEN = "A" * 64
PARTNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"


@pytest.fixture
def context(monkeypatch):
    app = Flask("cafe-logout-all-api-test")
    app.config["TESTING"] = True
    app.register_blueprint(routes.cafe_auth_bp)

    auth = Mock(return_value={
        "partner_id": PARTNER,
        "venue_id": VENUE,
        "account_type": "cafe_partner",
    })
    revoke_all = Mock(return_value=3)

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", auth
    )
    monkeypatch.setattr(
        routes, "revoke_all_cafe_sessions", revoke_all
    )

    return SimpleNamespace(
        client=app.test_client(),
        auth=auth,
        revoke_all=revoke_all,
    )


def _headers(value=TOKEN):
    return {"Authorization": "Bearer " + value}


def _assert_no_store(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_success_revokes_current_partner_only(context):
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 200
    _assert_no_store(response)
    assert response.get_json() == {
        "message": "All cafe sessions logged out.",
        "revoked_sessions": 3,
    }
    context.auth.assert_called_once_with(token=TOKEN)
    context.revoke_all.assert_called_once_with(partner_id=PARTNER)


def test_zero_active_sessions_is_success(context):
    context.revoke_all.return_value = 0
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 200
    assert response.get_json()["revoked_sessions"] == 0


def test_client_supplied_partner_id_cannot_override_owner(context):
    response = context.client.post(
        PATH,
        json={"partner_id": "507f1f77bcf86cd799439022"},
        headers=_headers(),
    )
    assert response.status_code == 200
    context.revoke_all.assert_called_once_with(partner_id=PARTNER)


def test_authenticated_other_partner_targets_only_that_partner(context):
    other = "507f1f77bcf86cd799439022"
    context.auth.return_value = {
        "partner_id": other,
        "venue_id": "507f191e810c19729de860bb",
        "account_type": "cafe_partner",
    }
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 200
    context.revoke_all.assert_called_once_with(partner_id=other)


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer short",
        "Basic " + TOKEN,
        "bearer " + TOKEN,
        "Bearer  " + TOKEN,
        "Bearer " + TOKEN + " extra",
    ],
)
def test_malformed_or_missing_bearer_rejected(context, authorization):
    headers = (
        {} if authorization is None
        else {"Authorization": authorization}
    )
    response = context.client.post(PATH, headers=headers)
    assert response.status_code == 401
    _assert_no_store(response)
    context.auth.assert_not_called()
    context.revoke_all.assert_not_called()


def test_expired_or_revoked_session_rejected(context):
    context.auth.side_effect = CafeSessionInvalid("expired")
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 401
    _assert_no_store(response)
    context.revoke_all.assert_not_called()
    assert response.get_json() == {"error": "Invalid or expired cafe session."}


def test_suspended_cafe_cannot_revoke_other_sessions(context):
    context.auth.side_effect = CafeSessionInvalid("suspended")
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 401
    context.revoke_all.assert_not_called()


@pytest.mark.parametrize(
    "invalid_context",
    [
        {},
        {"partner_id": None},
        {"partner_id": 123},
    ],
)
def test_missing_or_invalid_partner_context_rejected(
    context, invalid_context
):
    context.auth.return_value = invalid_context
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 401
    _assert_no_store(response)
    context.revoke_all.assert_not_called()


def test_session_storage_outage_fails_closed(context):
    context.auth.side_effect = PyMongoError("db unavailable")
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 503
    _assert_no_store(response)
    context.revoke_all.assert_not_called()
    assert "db unavailable" not in response.get_data(as_text=True)


def test_revocation_storage_outage_fails_closed(context):
    context.revoke_all.side_effect = PyMongoError("db unavailable")
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 503
    _assert_no_store(response)
    assert "db unavailable" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "invalid_count",
    [None, False, True, -1, "3", 1.2],
)
def test_invalid_revocation_count_never_reports_success(
    context, invalid_count
):
    context.revoke_all.return_value = invalid_count
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 503
    _assert_no_store(response)


def test_logout_all_is_post_only(context):
    response = context.client.get(PATH, headers=_headers())
    assert response.status_code == 405
    context.auth.assert_not_called()
    context.revoke_all.assert_not_called()


def test_no_session_credentials_returned(context):
    response = context.client.post(PATH, headers=_headers())
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert TOKEN not in body
    assert "access_token" not in body
    assert "password" not in body.lower()
    assert PARTNER not in body
    assert VENUE not in body


def test_logout_all_only_registered_once(context):
    matches = [
        rule for rule in context.client.application.url_map.iter_rules()
        if rule.rule == PATH
    ]
    assert len(matches) == 1
    assert "POST" in matches[0].methods
