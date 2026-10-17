"""Protected cafe venue HTTP API security tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import cafe_venue_routes as routes
from app.repositories.venue_repository import VenueRepositoryError
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import CafeSessionInvalid
from app.services.cafe_venue_management_service import (
    CafeVenueManagementDenied,
)


TOKEN = "A" * 64
OWNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_OWNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"
PATH = "/cafe-partners/venue"


@pytest.fixture
def context(monkeypatch):
    app = Flask("xuoroni-cafe-venue-http-security")
    app.config["TESTING"] = True
    app.register_blueprint(routes.cafe_venue_bp)

    authenticate = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": OWNER,
        "venue_id": VENUE,
    })
    get_profile = Mock(return_value={
        "venue_id": VENUE,
        "venue_name": "Cafe Xuoroni",
        "status": "approved",
        "active": True,
        "city": "Guwahati",
    })

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", authenticate
    )
    monkeypatch.setattr(
        routes, "get_cafe_owner_venue_profile", get_profile
    )

    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        authenticate=authenticate,
        get_profile=get_profile,
    )


def headers(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def assert_uncached(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_approved_owner_receives_venue(context):
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 200
    assert_uncached(response)
    assert response.get_json()["venue"]["venue_id"] == VENUE
    assert response.get_json()["venue"]["venue_name"] == "Cafe Xuoroni"
    context.authenticate.assert_called_once_with(token=TOKEN)
    context.get_profile.assert_called_once_with(
        partner_id=OWNER, venue_id=VENUE
    )


def test_only_the_authenticated_identity_is_used(context):
    response = context.client.get(
        PATH + "?partner_id=" + OTHER_OWNER
        + "&venue_id=" + OTHER_VENUE,
        headers=headers(),
    )
    assert response.status_code == 200
    context.get_profile.assert_called_once_with(
        partner_id=OWNER, venue_id=VENUE
    )


def test_different_authenticated_owner_uses_own_venue(context):
    context.authenticate.return_value = {
        "account_type": "cafe_partner",
        "partner_id": OTHER_OWNER,
        "venue_id": OTHER_VENUE,
    }
    context.get_profile.return_value = {
        "venue_id": OTHER_VENUE,
        "venue_name": "Second Cafe",
        "status": "approved",
        "active": True,
    }

    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 200
    assert response.get_json()["venue"]["venue_id"] == OTHER_VENUE
    context.get_profile.assert_called_once_with(
        partner_id=OTHER_OWNER, venue_id=OTHER_VENUE
    )


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
        "Bearer " + TOKEN + ",Bearer X",
        "Bearer " + "A" * 300,
    ],
)
def test_malformed_authorization_rejected(context, authorization):
    request_headers = (
        {} if authorization is None
        else {"Authorization": authorization}
    )
    response = context.client.get(PATH, headers=request_headers)
    assert response.status_code == 401
    assert_uncached(response)
    context.authenticate.assert_not_called()
    context.get_profile.assert_not_called()


@pytest.mark.parametrize(
    "invalid_context",
    [
        None,
        [],
        {},
        {"account_type": "dating_user",
         "partner_id": OWNER, "venue_id": VENUE},
        {"account_type": "superadmin",
         "partner_id": OWNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": None, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": 123, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": OWNER, "venue_id": None},
        {"account_type": "cafe_partner",
         "partner_id": OWNER, "venue_id": "invalid"},
    ],
)
def test_invalid_session_identity_rejected(
    context, invalid_context
):
    context.authenticate.return_value = invalid_context
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)
    context.get_profile.assert_not_called()


def test_expired_session_rejected(context):
    context.authenticate.side_effect = CafeSessionInvalid(
        "internal expired token detail"
    )
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)
    assert "internal expired token detail" not in response.get_data(
        as_text=True
    )
    context.get_profile.assert_not_called()


def test_revoked_session_rejected(context):
    context.authenticate.side_effect = CafeSessionInvalid(
        "revoked"
    )
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    context.get_profile.assert_not_called()


def test_suspended_cafe_rejected(context):
    context.get_profile.side_effect = CafePartnerAccessDenied(
        "Internal cafe suspension"
    )
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)
    assert "Internal cafe suspension" not in response.get_data(
        as_text=True
    )


def test_cross_owner_profile_access_rejected(context):
    context.get_profile.side_effect = CafeVenueManagementDenied(
        "Owner mismatch"
    )
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)


def test_invalid_repository_identifier_rejected(context):
    context.get_profile.side_effect = VenueRepositoryError(
        "Invalid venue identifier"
    )
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert_uncached(response)


@pytest.mark.parametrize("failing_stage", ["session", "profile"])
def test_mongodb_outage_fails_closed(context, failing_stage):
    if failing_stage == "session":
        context.authenticate.side_effect = PyMongoError(
            "PRIVATE_SESSION_DATABASE_DETAIL"
        )
    else:
        context.get_profile.side_effect = PyMongoError(
            "PRIVATE_PROFILE_DATABASE_DETAIL"
        )

    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 503
    assert_uncached(response)
    assert "PRIVATE_" not in response.get_data(
        as_text=True
    )


@pytest.mark.parametrize("invalid_profile", [None, [], "invalid", 123])
def test_invalid_profile_service_response_rejected(
    context, invalid_profile
):
    context.get_profile.return_value = invalid_profile
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 503
    assert_uncached(response)


def test_read_endpoint_rejects_writes(context):
    # The same URL now intentionally supports GET and PATCH.
    for method in ("post", "put", "delete"):
        response = getattr(context.client, method)(
            PATH, headers=headers()
        )
        assert response.status_code == 405

    # Authenticated PATCH still requires valid JSON.
    response = context.client.patch(PATH, headers=headers())
    assert response.status_code == 415
    assert_uncached(response)

    # A valid PATCH body without a valid cafe session is denied.
    context.authenticate.side_effect = CafeSessionInvalid(
        "Invalid cafe session."
    )
    response = context.client.patch(
        PATH,
        json={"description": "Unauthorized change"},
        headers=headers(),
    )
    assert response.status_code == 401
    assert_uncached(response)

    context.get_profile.assert_not_called()



def test_response_does_not_expose_session_credentials(context):
    response = context.client.get(PATH, headers=headers())
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert TOKEN not in body
    assert "access_token" not in body
    assert "password_hash" not in body
    assert "owner_email" not in body


def test_venue_route_registered_once(context):
    matches = [
        rule for rule in context.app.url_map.iter_rules()
        if rule.rule == PATH
    ]
    get_rules = [
        rule for rule in matches
        if "GET" in rule.methods
    ]
    patch_rules = [
        rule for rule in matches
        if "PATCH" in rule.methods
    ]

    assert len(matches) == 2
    assert len(get_rules) == 1
    assert len(patch_rules) == 1
    assert "PATCH" not in get_rules[0].methods
    assert "GET" not in patch_rules[0].methods
