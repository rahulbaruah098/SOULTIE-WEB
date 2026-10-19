"""HTTP acceptance for authenticated cafe booking decisions."""

from unittest.mock import Mock

import pytest
from bson import ObjectId
from flask import Flask

from app.api.v1 import cafe_booking_action_routes as routes


@pytest.fixture
def context(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.cafe_booking_action_bp)

    reservation = str(ObjectId())
    partner = str(ObjectId())
    venue = str(ObjectId())

    service = Mock(return_value={
        "reservation_id": reservation,
        "venue_id": venue,
        "previous_status": "pending",
        "status": "confirmed",
    })

    authentication = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": partner,
        "venue_id": venue,
    })

    monkeypatch.setattr(
        routes, "authenticate_cafe_session", authentication
    )
    monkeypatch.setattr(
        routes, "change_cafe_reservation_status", service
    )

    return app.test_client(), reservation, partner, venue, service, authentication


def endpoint(reservation):
    return f"/cafe-partners/bookings/{reservation}/decision"


def headers():
    return {"Authorization": "Bearer " + "a" * 64}


def test_missing_authentication_is_denied(context):
    client, reservation, _, _, service, _ = context
    result = client.post(
        endpoint(reservation),
        json={"decision": "confirmed"},
    )
    assert result.status_code == 401
    service.assert_not_called()


def test_valid_actor_uses_session_owned_venue(context):
    client, reservation, partner, venue, service, _ = context

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 200
    assert result.json["venue_id"] == venue
    assert result.json["status"] == "confirmed"

    service.assert_called_once_with(
        reservation_id=reservation,
        actor_id=partner,
        actor_role="cafe_partner",
        decision="confirmed",
        venue_id=venue,
    )


@pytest.mark.parametrize("body", [
    {"decision": "confirmed", "venue_id": "other"},
    {"decision": "confirmed", "actor_id": "other"},
    {"decision": "invalid"},
    {"decision": True},
    {},
    ["confirmed"],
])
def test_client_identity_and_invalid_payload_rejected(context, body):
    client, reservation, _, _, service, _ = context

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json=body,
    )

    assert result.status_code == 400
    service.assert_not_called()


def test_wrong_account_type_denied(context):
    client, reservation, _, venue, service, authentication = context

    authentication.return_value = {
        "account_type": "user",
        "partner_id": str(ObjectId()),
        "venue_id": venue,
    }

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 401
    service.assert_not_called()


def test_cross_venue_service_denial(context):
    client, reservation, _, _, service, _ = context

    service.side_effect = routes.CafeReservationLifecycleDenied(
        "Reservation belongs to another venue."
    )

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 403


def test_invalid_transition_conflict(context):
    client, reservation, _, _, service, _ = context

    service.side_effect = routes.CafeReservationLifecycleConflict(
        "Invalid transition."
    )

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 409


def test_unverified_service_acknowledgement_fails_closed(context):
    client, reservation, _, _, service, _ = context

    service.return_value = {
        "reservation_id": reservation,
        "venue_id": str(ObjectId()),
        "status": "confirmed",
    }

    result = client.post(
        endpoint(reservation),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 503
    assert result.headers["Cache-Control"] == "no-store"


def test_invalid_reservation_id_denied(context):
    client, _, _, _, service, _ = context

    result = client.post(
        endpoint("invalid"),
        headers=headers(),
        json={"decision": "confirmed"},
    )

    assert result.status_code == 400
    service.assert_not_called()