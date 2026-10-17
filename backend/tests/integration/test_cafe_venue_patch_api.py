"""Permanent HTTP security tests for cafe venue PATCH operations."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import cafe_venue_routes as routes
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import CafeSessionInvalid
from app.services.cafe_venue_management_service import (
    CafeVenueManagementDenied,
)


PATH = "/cafe-partners/venue"
TOKEN = "A" * 64
OWNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_OWNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"


@pytest.fixture
def context(monkeypatch):
    app = Flask("xuoroni-cafe-patch-security")
    app.config["TESTING"] = True
    app.register_blueprint(routes.cafe_venue_bp)

    auth = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": OWNER,
        "venue_id": VENUE,
    })

    update = Mock(return_value={
        "venue_id": VENUE,
        "status": "pending",
        "requires_reapproval": True,
        "message": "Venue changes submitted for review.",
    })

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", auth
    )
    monkeypatch.setattr(
        routes, "update_cafe_owner_venue_profile", update
    )

    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        auth=auth,
        update=update,
    )


def headers():
    return {"Authorization": "Bearer " + TOKEN}


def check_private(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_valid_update_requires_superadmin_reapproval(context):
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Updated cafe description"},
    )
    assert response.status_code == 200
    check_private(response)
    assert response.get_json() == {
        "venue_id": VENUE,
        "status": "pending",
        "requires_reapproval": True,
        "message": "Venue changes submitted for review.",
    }
    context.auth.assert_called_once_with(token=TOKEN)
    context.update.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
        changes={"description": "Updated cafe description"},
    )


def test_fields_are_trimmed_by_existing_schema(context):
    response = context.client.patch(
        PATH, headers=headers(),
        json={"venue_name": "  Test Cafe  "},
    )
    assert response.status_code == 200
    assert context.update.call_args.kwargs["changes"] == {
        "venue_name": "Test Cafe"
    }


def test_query_and_payload_cannot_override_owner(context):
    response = context.client.patch(
        PATH + "?partner_id=" + OTHER_OWNER
        + "&venue_id=" + OTHER_VENUE,
        headers=headers(),
        json={"city": "Guwahati"},
    )
    assert response.status_code == 200
    context.update.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
        changes={"city": "Guwahati"},
    )


@pytest.mark.parametrize(
    "protected",
    [
        {"owner_id": OTHER_OWNER},
        {"status": "approved"},
        {"active": True},
        {"reviewed_by": OWNER},
        {"status_version": 100},
        {"password_hash": "unsafe"},
        {"venue_id": OTHER_VENUE},
        {"account_type": "superadmin"},
    ],
)
def test_protected_fields_rejected_before_write(context, protected):
    response = context.client.patch(
        PATH, headers=headers(), json=protected
    )
    assert response.status_code == 400
    check_private(response)
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        [],
        None,
        "plain string",
        {"description": 123},
        {"description": {"nested": "invalid"}},
    ],
)
def test_invalid_json_shapes_rejected(context, invalid):
    if invalid is None:
        response = context.client.patch(
            PATH,
            headers=headers(),
            data="null",
            content_type="application/json",
        )
    else:
        response = context.client.patch(
            PATH, headers=headers(), json=invalid
        )
    assert response.status_code == 400
    check_private(response)
    context.update.assert_not_called()


def test_explicit_malformed_json_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        data='{"description":',
        content_type="application/json",
    )
    assert response.status_code == 400
    context.update.assert_not_called()


def test_missing_json_content_type_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        data="description=hello",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 415
    check_private(response)
    context.update.assert_not_called()


def test_oversized_json_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        json={"description": "A" * 5000},
    )
    assert response.status_code == 413
    check_private(response)
    context.update.assert_not_called()


def test_missing_session_rejected_before_write(context):
    response = context.client.patch(
        PATH, json={"description": "Unauthorized"}
    )
    assert response.status_code == 401
    context.auth.assert_not_called()
    context.update.assert_not_called()


def test_revoked_or_expired_session_rejected(context):
    context.auth.side_effect = CafeSessionInvalid(
        "private token information"
    )
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Unauthorized"},
    )
    assert response.status_code == 401
    assert "private token information" not in response.get_data(
        as_text=True
    )
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "bad_identity",
    [
        {},
        None,
        {"account_type": "dating_user",
         "partner_id": OWNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": None, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": OWNER, "venue_id": "bad"},
    ],
)
def test_invalid_session_identity_denied(context, bad_identity):
    context.auth.return_value = bad_identity
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Unauthorized"},
    )
    assert response.status_code == 401
    context.update.assert_not_called()


def test_suspended_cafe_denied(context):
    context.update.side_effect = CafePartnerAccessDenied(
        "private suspension reason"
    )
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Unauthorized"},
    )
    assert response.status_code == 401
    assert "private suspension reason" not in response.get_data(
        as_text=True
    )


def test_cross_owner_repository_denial(context):
    context.update.side_effect = CafeVenueManagementDenied(
        "cross owner"
    )
    response = context.client.patch(
        PATH, headers=headers(),
        json={"venue_name": "Another Cafe"},
    )
    assert response.status_code == 401
    check_private(response)


def test_database_failure_returns_503(context):
    context.update.side_effect = PyMongoError(
        "private database detail"
    )
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Valid"},
    )
    assert response.status_code == 503
    check_private(response)
    assert "private database detail" not in response.get_data(
        as_text=True
    )


@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        {},
        [],
        {"venue_id": OTHER_VENUE, "status": "pending",
         "requires_reapproval": True},
        {"venue_id": VENUE, "status": "approved",
         "requires_reapproval": True},
        {"venue_id": VENUE, "status": "pending",
         "requires_reapproval": False},
    ],
)
def test_invalid_service_acknowledgement_not_success(
    context, invalid_result
):
    context.update.return_value = invalid_result
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Valid"},
    )
    assert response.status_code == 503
    check_private(response)


def test_response_does_not_return_credentials(context):
    response = context.client.patch(
        PATH, headers=headers(),
        json={"description": "Valid"},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for secret in (
        TOKEN,
        "password_hash",
        "access_token",
        OTHER_OWNER,
        OTHER_VENUE,
    ):
        assert secret not in body


def test_patch_registered_once_alongside_get(context):
    rules = [
        rule for rule in context.app.url_map.iter_rules()
        if rule.rule == PATH
    ]
    assert sum("PATCH" in rule.methods for rule in rules) == 1
    assert sum("GET" in rule.methods for rule in rules) == 1


def test_unsupported_methods_remain_rejected(context):
    for method in ("post", "put", "delete"):
        response = getattr(context.client, method)(PATH)
        assert response.status_code == 405
    context.update.assert_not_called()
