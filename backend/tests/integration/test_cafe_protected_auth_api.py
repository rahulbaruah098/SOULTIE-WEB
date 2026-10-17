"""Protected cafe-session HTTP routes: isolated security tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import cafe_auth_routes as routes
from app.services.cafe_partner_session_service import CafeSessionInvalid


TOKEN = "A" * 64
ME = "/cafe-partners/me"
LOGOUT = "/cafe-partners/logout"
CONTEXT = {
    "partner_id": "507f1f77bcf86cd799439011",
    "venue_id": "507f191e810c19729de860ea",
    "account_type": "cafe_partner",
}


@pytest.fixture
def context(monkeypatch):
    app = Flask("cafe-protected-auth-security")
    app.config["TESTING"] = True
    app.register_blueprint(routes.cafe_auth_bp)

    authenticate = Mock(return_value=dict(CONTEXT))
    revoke = Mock(return_value=True)

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", authenticate
    )
    monkeypatch.setattr(
        routes, "revoke_cafe_session", revoke
    )

    return SimpleNamespace(
        client=app.test_client(),
        app=app,
        authenticate=authenticate,
        revoke=revoke,
    )


def headers(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def assert_uncached(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_me_returns_only_current_cafe_identity(context):
    response = context.client.get(ME, headers=headers())
    assert response.status_code == 200
    assert_uncached(response)
    assert response.get_json() == CONTEXT
    context.authenticate.assert_called_once_with(token=TOKEN)
    context.revoke.assert_not_called()


def test_me_never_returns_token_or_private_fields(context):
    response = context.client.get(ME, headers=headers())
    body = response.get_data(as_text=True)
    assert TOKEN not in body
    assert "password" not in body.lower()
    assert "email" not in response.get_json()
    assert "user_id" not in response.get_json()
    assert "admin_id" not in response.get_json()


def test_me_respects_authorized_partner_and_venue(context):
    second = {
        "partner_id": "507f1f77bcf86cd799439022",
        "venue_id": "507f191e810c19729de860bb",
        "account_type": "cafe_partner",
    }
    context.authenticate.return_value = second
    response = context.client.get(ME, headers=headers())
    assert response.status_code == 200
    assert response.get_json() == second
    assert response.get_json()["partner_id"] != CONTEXT["partner_id"]


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "bearer " + TOKEN,
        "Basic " + TOKEN,
        "Bearer  " + TOKEN,
        "Bearer " + TOKEN + ",Bearer X",
        "Bearer " + ("A" * 300),
        "Bearer short",
        "Bearer " + TOKEN + " extra",
    ],
)
def test_me_rejects_missing_and_malformed_headers(context, header):
    request_headers = (
        {} if header is None
        else {"Authorization": header}
    )
    response = context.client.get(ME, headers=request_headers)
    assert response.status_code == 401
    assert_uncached(response)
    context.authenticate.assert_not_called()


def test_me_rejects_expired_or_revoked_session(context):
    context.authenticate.side_effect = CafeSessionInvalid(
        "Expired or revoked"
    )
    response = context.client.get(ME, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)
    assert "Expired or revoked" not in response.get_data(
        as_text=True
    )


def test_me_rejects_suspended_cafe(context):
    context.authenticate.side_effect = CafeSessionInvalid(
        "Venue suspended"
    )
    response = context.client.get(ME, headers=headers())
    assert response.status_code == 401
    assert "Venue suspended" not in response.get_data(
        as_text=True
    )


def test_me_fails_closed_on_database_outage(context):
    context.authenticate.side_effect = PyMongoError(
        "database offline"
    )
    response = context.client.get(ME, headers=headers())
    assert response.status_code == 503
    assert_uncached(response)
    assert "database offline" not in response.get_data(
        as_text=True
    )


def test_logout_revokes_only_presented_cafe_token(context):
    response = context.client.post(LOGOUT, headers=headers())
    assert response.status_code == 200
    assert_uncached(response)
    assert response.get_json() == {
        "message": "Cafe session logged out."
    }
    context.revoke.assert_called_once_with(token=TOKEN)
    context.authenticate.assert_not_called()


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic " + TOKEN, "Bearer short"],
)
def test_logout_rejects_malformed_token(context, header):
    request_headers = (
        {} if header is None
        else {"Authorization": header}
    )
    response = context.client.post(
        LOGOUT, headers=request_headers
    )
    assert response.status_code == 401
    assert_uncached(response)
    context.revoke.assert_not_called()


def test_logout_rejects_unknown_or_already_revoked_token(context):
    context.revoke.return_value = False
    response = context.client.post(LOGOUT, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)


def test_logout_rejects_invalid_session_exception(context):
    context.revoke.side_effect = CafeSessionInvalid(
        "Malformed or expired"
    )
    response = context.client.post(LOGOUT, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)


def test_logout_fails_closed_on_database_outage(context):
    context.revoke.side_effect = PyMongoError(
        "database offline"
    )
    response = context.client.post(LOGOUT, headers=headers())
    assert response.status_code == 503
    assert_uncached(response)
    assert "database offline" not in response.get_data(
        as_text=True
    )


def test_me_get_only_and_logout_post_only(context):
    assert context.client.post(ME).status_code == 405
    assert context.client.get(LOGOUT).status_code == 405
    context.authenticate.assert_not_called()
    context.revoke.assert_not_called()


def test_no_dating_user_or_superadmin_route_added(context):
    paths = {rule.rule for rule in context.app.url_map.iter_rules()}
    assert ME in paths
    assert LOGOUT in paths
    assert "/auth/login" not in paths
    assert "/admin/login" not in paths
