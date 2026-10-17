"""Permanent mocked cafe booking notification recipient security tests.

These verify persisted-identity lookups and fail-closed routing.
No real database, email, push or websocket delivery is performed.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.services import (
    cafe_booking_recipient_routing_service as svc,
)


R = "507f1f77bcf86cd799439099"
V = "507f191e810c19729de860ea"
C = "507f1f77bcf86cd799439011"
P = "507f1f77bcf86cd799439012"

EVENTS = (
    "booking_created",
    "booking_confirmed",
    "booking_rejected",
    "booking_cancelled",
    "booking_completed",
    "booking_no_show",
)


def event(kind):
    return {
        "reservation_id": R,
        "venue_id": V,
        "customer_id": C,
        "event_type": kind,
        "event_key": f"{R}:{kind}",
    }


@pytest.fixture
def context(monkeypatch):
    reservations = Mock()
    venues = Mock()
    partners = Mock()
    users = Mock()

    reservation = {
        "_id": ObjectId(R),
        "venue_id": ObjectId(V),
        "customer_id": ObjectId(C),
        "status": "pending",
    }
    venue = {
        "_id": ObjectId(V),
        "owner_id": P,
        "status": "approved",
        "active": True,
    }
    partner = {
        "_id": ObjectId(P),
        "venue_id": ObjectId(V),
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
    }
    customer = {
        "_id": ObjectId(C),
        "account_status": "active",
    }

    reservations.find_one.return_value = deepcopy(reservation)
    venues.find_one.return_value = deepcopy(venue)
    partners.find_one.return_value = deepcopy(partner)
    users.find_one.return_value = deepcopy(customer)

    database = SimpleNamespace(
        reservations=reservations,
        venues=venues,
        cafe_partner_accounts=partners,
        users=users,
    )
    monkeypatch.setattr(
        svc, "mongo", SimpleNamespace(db=database)
    )

    return SimpleNamespace(
        reservations=reservations,
        venues=venues,
        partners=partners,
        users=users,
        reservation=reservation,
        venue=venue,
        partner=partner,
        customer=customer,
    )


def resolve(kind):
    return svc.resolve_cafe_booking_recipients(
        event=event(kind)
    )


def test_new_booking_targets_only_venue_partner(context):
    result = resolve("booking_created")
    assert result["recipients"] == [{
        "recipient_type": "cafe_partner",
        "recipient_id": P,
        "delivery_key": f"{R}:booking_created:cafe_partner:{P}",
    }]
    context.users.find_one.assert_not_called()


@pytest.mark.parametrize("kind", EVENTS[1:])
def test_lifecycle_routes_to_both_parties(context, kind):
    result = resolve(kind)
    recipients = result["recipients"]
    assert [r["recipient_type"] for r in recipients] == [
        "customer", "cafe_partner"
    ]
    assert [r["recipient_id"] for r in recipients] == [C, P]
    assert len({r["delivery_key"] for r in recipients}) == 2
    assert all(r["delivery_key"].startswith(
        f"{R}:{kind}:"
    ) for r in recipients)


def test_database_queries_use_verified_identity(context):
    resolve("booking_confirmed")
    context.reservations.find_one.assert_called_once_with({
        "_id": ObjectId(R),
        "venue_id": ObjectId(V),
        "customer_id": ObjectId(C),
    })
    partner_filter = context.partners.find_one.call_args.args[0]
    assert partner_filter["_id"] == ObjectId(P)
    assert partner_filter["venue_id"] == ObjectId(V)
    assert partner_filter["account_type"] == "cafe_partner"


@pytest.mark.parametrize("field", (
    "reservation_id", "venue_id", "customer_id"
))
def test_forged_event_identity_rejected(context, field):
    values = event("booking_confirmed")
    values[field] = str(ObjectId())
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        svc.resolve_cafe_booking_recipients(event=values)


@pytest.mark.parametrize("bad_key", (
    None, "", "wrong", "507f1f77bcf86cd799439099:booking_created"
))
def test_mismatched_delivery_event_key_rejected(
    context, bad_key
):
    values = event("booking_confirmed")
    values["event_key"] = bad_key
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        svc.resolve_cafe_booking_recipients(event=values)
    context.reservations.find_one.assert_not_called()


@pytest.mark.parametrize("kind", (
    "", "booking_approved", None, "admin_override"
))
def test_unsupported_event_rejected(context, kind):
    values = event("booking_created")
    values["event_type"] = kind
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        svc.resolve_cafe_booking_recipients(event=values)
    context.reservations.find_one.assert_not_called()


def test_missing_reservation_rejected(context):
    context.reservations.find_one.return_value = None
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_created")
    context.venues.find_one.assert_not_called()


def test_mismatched_persisted_customer_rejected(context):
    context.reservations.find_one.return_value = {
        **context.reservation,
        "customer_id": ObjectId(),
    }
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_created")
    context.venues.find_one.assert_not_called()


@pytest.mark.parametrize("change", (
    {"status": "suspended"},
    {"active": False},
    {"owner_id": str(ObjectId())},
))
def test_venue_policy_and_owner_change_rejected(
    context, change
):
    context.venues.find_one.return_value = {
        **context.venue, **change
    }
    if change.get("owner_id") is not None:
        # A newly assigned owner needs separately verified credentials;
        # an old partner record cannot authorize that new owner.
        context.partners.find_one.return_value = context.partner
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_created")


@pytest.mark.parametrize("change", (
    {"status": "suspended"},
    {"email_verified": False},
    {"account_type": "dating_user"},
    {"venue_id": ObjectId()},
    {"_id": ObjectId()},
))
def test_ineligible_cafe_partner_rejected(context, change):
    context.partners.find_one.return_value = {
        **context.partner, **change
    }
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_created")


@pytest.mark.parametrize("status", (
    "suspended", "banned", "deleted"
))
def test_blocked_customer_rejected(context, status):
    context.users.find_one.return_value = {
        **context.customer,
        "account_status": status,
    }
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_confirmed")


def test_missing_customer_rejected(context):
    context.users.find_one.return_value = None
    with pytest.raises(svc.CafeBookingRecipientRoutingError):
        resolve("booking_confirmed")


def test_customer_lookup_is_restricted(context):
    resolve("booking_confirmed")
    query = context.users.find_one.call_args.args[0]
    assert query["_id"] == ObjectId(C)
    assert "$nin" in query["account_status"]


def test_result_excludes_private_contact_details(context):
    result = resolve("booking_confirmed")
    assert set(result) == {
        "event_key", "event_type", "recipients"
    }
    for recipient in result["recipients"]:
        assert set(recipient) == {
            "recipient_type",
            "recipient_id",
            "delivery_key",
        }


@pytest.mark.parametrize("collection_name", (
    "reservations", "venues", "partners", "users"
))
def test_database_error_propagates(context, collection_name):
    collection = getattr(context, collection_name)
    collection.find_one.side_effect = OperationFailure(
        "simulated database failure"
    )
    with pytest.raises(OperationFailure):
        resolve("booking_confirmed")
