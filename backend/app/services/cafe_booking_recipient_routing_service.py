"""Server-only recipient routing for Xuoroni cafe booking events.

Revalidate the durable event against its persisted reservation and
the current customer, cafe-partner, and venue account records.

No addresses, tokens, contact numbers, or other personal information
are returned. This service never sends or acknowledges notifications.

Callers must not treat an empty recipient list as successful delivery;
a future dispatcher adapter must implement an explicit policy for
unavailable or ineligible recipients.
"""

from bson import ObjectId

from app.extensions import mongo


class CafeBookingRecipientRoutingError(ValueError):
    """Booking event or recipient mapping cannot be trusted."""


_EVENT_TYPES = frozenset({
    "booking_created",
    "booking_confirmed",
    "booking_rejected",
    "booking_cancelled",
    "booking_completed",
    "booking_no_show",
})

# Creation is owner-facing. Subsequent actions inform both parties;
# an individual delivery policy will handle per-recipient receipts.
_RECIPIENT_ROLES = {
    "booking_created": ("cafe_partner",),
    "booking_confirmed": ("customer", "cafe_partner"),
    "booking_rejected": ("customer", "cafe_partner"),
    "booking_cancelled": ("customer", "cafe_partner"),
    "booking_completed": ("customer", "cafe_partner"),
    "booking_no_show": ("customer", "cafe_partner"),
}


def _id(value, label):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeBookingRecipientRoutingError(
            f"Invalid {label}."
        )
    return ObjectId(value)


def resolve_cafe_booking_recipients(*, event):
    """Resolve live, authorized recipients for one claimed event.

    This is a read-only decision. The caller must separately manage
    retry and per-recipient delivery idempotency.
    """
    if not isinstance(event, dict):
        raise CafeBookingRecipientRoutingError(
            "Invalid booking event."
        )

    event_type = event.get("event_type")
    event_key = event.get("event_key")

    if event_type not in _EVENT_TYPES:
        raise CafeBookingRecipientRoutingError(
            "Unsupported booking event type."
        )

    reservation_id = _id(
        event.get("reservation_id"), "reservation ID"
    )
    venue_id = _id(event.get("venue_id"), "venue ID")
    customer_id = _id(
        event.get("customer_id"), "customer ID"
    )

    if event_key != f"{reservation_id}:{event_type}":
        raise CafeBookingRecipientRoutingError(
            "Booking event identity mismatch."
        )

    reservation = mongo.db.reservations.find_one({
        "_id": reservation_id,
        "venue_id": venue_id,
        "customer_id": customer_id,
    })

    if (
        not isinstance(reservation, dict)
        or reservation.get("_id") != reservation_id
        or reservation.get("venue_id") != venue_id
        or reservation.get("customer_id") != customer_id
    ):
        raise CafeBookingRecipientRoutingError(
            "Booking event has no verified reservation."
        )

    venue = mongo.db.venues.find_one({
        "_id": venue_id,
        "status": "approved",
        "active": True,
    })

    if (
        not isinstance(venue, dict)
        or venue.get("_id") != venue_id
        or venue.get("status") != "approved"
        or venue.get("active") is not True
    ):
        raise CafeBookingRecipientRoutingError(
            "Booking venue is unavailable."
        )

    owner_id = venue.get("owner_id")
    owner_oid = _id(owner_id, "venue owner ID")

    partner = mongo.db.cafe_partner_accounts.find_one({
        "_id": owner_oid,
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
        "venue_id": venue_id,
    })

    if (
        not isinstance(partner, dict)
        or partner.get("_id") != owner_oid
        or partner.get("venue_id") != venue_id
        or partner.get("account_type") != "cafe_partner"
        or partner.get("status") != "approved"
        or partner.get("email_verified") is not True
    ):
        raise CafeBookingRecipientRoutingError(
            "Booking venue partner is unavailable."
        )

    recipients = []

    if "customer" in _RECIPIENT_ROLES[event_type]:
        customer = mongo.db.users.find_one({
            "_id": customer_id,
            "account_status": {"$nin": [
                "suspended", "banned", "deleted"
            ]},
        })

        if (
            not isinstance(customer, dict)
            or customer.get("_id") != customer_id
            or customer.get("account_status") in {
                "suspended", "banned", "deleted"
            }
        ):
            raise CafeBookingRecipientRoutingError(
                "Booking customer is unavailable."
            )

        recipients.append({
            "recipient_type": "customer",
            "recipient_id": str(customer_id),
            "delivery_key": (
                f"{event_key}:customer:{customer_id}"
            ),
        })

    if "cafe_partner" in _RECIPIENT_ROLES[event_type]:
        recipients.append({
            "recipient_type": "cafe_partner",
            "recipient_id": str(owner_oid),
            "delivery_key": (
                f"{event_key}:cafe_partner:{owner_oid}"
            ),
        })

    return {
        "event_key": event_key,
        "event_type": event_type,
        "recipients": recipients,
    }
