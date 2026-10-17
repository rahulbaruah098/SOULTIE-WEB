"""HTTP security contracts for Xuoroni cafe operations API."""

from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
from pymongo.errors import PyMongoError
import pytest

from app.api.v1 import api_v1_bp
from app.api.v1 import cafe_venue_operations_routes as routes
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import CafeSessionInvalid


PATH = "/api/v1/cafe-partners/venue/operations"
OWNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_OWNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"
TOKEN = "A" * 64

SETTINGS = {
    "max_party_size": 8,
    "slot_minutes": 60,
    "advance_booking_days": 30,
    "buffer_minutes": 15,
}
HOURS = {
    day: [{"open": "09:00", "close": "18:00"}]
    for day in (
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday",
    )
}


def headers():
    return {"Authorization": "Bearer " + TOKEN}


def private(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.fixture
def context(monkeypatch):
    app = Flask("xuoroni-cafe-operations-api-tests")
    app.config["TESTING"] = True
    app.register_blueprint(api_v1_bp)

    auth = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": OWNER,
        "venue_id": VENUE,
    })
    update = Mock(return_value={
        "venue_id": VENUE,
        "facilities": ["parking", "wifi"],
        "status": "approved",
        "message": "Cafe facilities updated.",
    })

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", auth
    )
    monkeypatch.setattr(
        routes, "update_cafe_owner_operations", update
    )

    return SimpleNamespace(
        app=app,
        client=app.test_client(),
        auth=auth,
        update=update,
    )


def test_registered_once_on_main_api(context):
    matches = [
        rule for rule in context.app.url_map.iter_rules()
        if rule.rule == PATH
    ]
    assert len(matches) == 1
    assert "PATCH" in matches[0].methods
    assert "GET" not in matches[0].methods


def test_authenticated_facilities_update(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        json={"facilities": ["wifi", "parking"]},
    )
    assert response.status_code == 200
    private(response)
    assert response.get_json() == {
        "venue_id": VENUE,
        "facilities": ["parking", "wifi"],
        "status": "approved",
        "message": "Cafe facilities updated.",
    }
    context.auth.assert_called_once_with(token=TOKEN)
    context.update.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
        changes={"facilities": ["parking", "wifi"]},
    )


def test_empty_facility_list_is_valid(context):
    context.update.return_value["facilities"] = []
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": []},
    )
    assert response.status_code == 200
    assert response.get_json()["facilities"] == []


def test_query_parameters_cannot_override_owner(context):
    response = context.client.patch(
        PATH + "?partner_id=" + OTHER_OWNER
        + "&venue_id=" + OTHER_VENUE,
        headers=headers(),
        json={"facilities": ["parking", "wifi"]},
    )
    assert response.status_code == 200
    context.update.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
        changes={"facilities": ["parking", "wifi"]},
    )


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic " + TOKEN,
        "Bearer ",
        "Bearer " + "A" * 10,
        "Bearer " + "A" * 301,
        "Bearer " + "A" * 40 + " " + "A" * 24,
        "Bearer " + "A" * 32 + "," + "A" * 32,
    ],
)
def test_invalid_bearer_rejected(context, authorization):
    given = (
        {} if authorization is None
        else {"Authorization": authorization}
    )
    response = context.client.patch(
        PATH, headers=given, json={"facilities": ["wifi"]},
    )
    assert response.status_code == 401
    private(response)
    context.auth.assert_not_called()
    context.update.assert_not_called()


def test_expired_or_revoked_session_rejected(context):
    context.auth.side_effect = CafeSessionInvalid("private detail")
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": ["wifi"]},
    )
    assert response.status_code == 401
    assert "private detail" not in response.get_data(as_text=True)
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "identity",
    [
        None,
        {},
        [],
        {
            "account_type": "dating_user",
            "partner_id": OWNER,
            "venue_id": VENUE,
        },
        {
            "account_type": "superadmin",
            "partner_id": OWNER,
            "venue_id": VENUE,
        },
        {
            "account_type": "cafe_partner",
            "partner_id": "bad",
            "venue_id": VENUE,
        },
        {
            "account_type": "cafe_partner",
            "partner_id": OWNER,
            "venue_id": "bad",
        },
    ],
)
def test_invalid_session_identity_rejected(context, identity):
    context.auth.return_value = identity
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": ["wifi"]},
    )
    assert response.status_code == 401
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        "invalid",
        {"status": "approved"},
        {"owner_id": OTHER_OWNER},
        {"active": True},
        {"venue_id": OTHER_VENUE},
        {"facilities": ["unknown"]},
        {"facilities": ["wifi", "wifi"]},
        {"facilities": "wifi"},
    ],
)
def test_invalid_payload_rejected_before_service(context, payload):
    response = context.client.patch(
        PATH, headers=headers(), json=payload,
    )
    assert response.status_code == 400
    private(response)
    context.update.assert_not_called()


def test_explicit_json_null_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        data="null",
        content_type="application/json",
    )
    assert response.status_code == 400
    context.update.assert_not_called()


def test_malformed_json_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        data='{"facilities":',
        content_type="application/json",
    )
    assert response.status_code == 400
    context.update.assert_not_called()


def test_non_json_request_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        data="facilities=wifi",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 415
    private(response)
    context.update.assert_not_called()


def test_oversized_body_rejected(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        json={"facilities": ["A" * 5000]},
    )
    assert response.status_code == 413
    private(response)
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"weekly_hours": HOURS},
        {"booking_settings": SETTINGS},
        {"facilities": ["wifi"], "weekly_hours": HOURS},
        {"facilities": ["wifi"], "booking_settings": SETTINGS},
    ],
)
def test_booking_sensitive_updates_return_409(context, payload):
    context.update.side_effect = routes.CafeVenueOperationsConflict(
        "sensitive settings restricted"
    )
    response = context.client.patch(
        PATH, headers=headers(), json=payload,
    )
    assert response.status_code == 409
    private(response)
    assert "sensitive settings restricted" not in (
        response.get_data(as_text=True)
    )


def test_suspended_cafe_denied(context):
    context.update.side_effect = CafePartnerAccessDenied(
        "private suspension details"
    )
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": ["wifi"]},
    )
    assert response.status_code == 401
    assert "private suspension details" not in (
        response.get_data(as_text=True)
    )


def test_cross_venue_denied(context):
    context.update.side_effect = routes.CafeVenueOperationsDenied(
        "cross owner"
    )
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": ["wifi"]},
    )
    assert response.status_code == 401
    private(response)


def test_database_outage_returns_503(context):
    context.update.side_effect = PyMongoError("private database detail")
    response = context.client.patch(
        PATH, headers=headers(), json={"facilities": ["wifi"]},
    )
    assert response.status_code == 503
    private(response)
    assert "private database detail" not in (
        response.get_data(as_text=True)
    )


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        [],
        {
            "venue_id": OTHER_VENUE,
            "status": "approved",
            "facilities": ["parking", "wifi"],
        },
        {
            "venue_id": VENUE,
            "status": "pending",
            "facilities": ["parking", "wifi"],
        },
        {
            "venue_id": VENUE,
            "status": "approved",
            "facilities": ["wifi"],
        },
    ],
)
def test_invalid_update_acknowledgement_fails_closed(context, result):
    context.update.return_value = result
    response = context.client.patch(
        PATH,
        headers=headers(),
        json={"facilities": ["parking", "wifi"]},
    )
    assert response.status_code == 503
    private(response)


def test_no_secret_data_in_success_response(context):
    response = context.client.patch(
        PATH,
        headers=headers(),
        json={"facilities": ["parking", "wifi"]},
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for secret in (
        TOKEN, OWNER, OTHER_OWNER, "password_hash",
        "access_token", "session_token",
    ):
        assert secret not in body


def test_unsupported_methods_return_405(context):
    for method in ("get", "post", "put", "delete"):
        response = getattr(context.client, method)(PATH)
        assert response.status_code == 405
    context.update.assert_not_called()
