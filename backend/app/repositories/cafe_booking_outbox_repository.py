"""Transaction-required durable outbox for Soultie cafe bookings.

Outbox writes MUST use the same MongoDB transaction session as the
reservation insert or lifecycle status update.

This repository never sends email, push, websocket messages, or
external requests. A separate worker will dispatch committed events.

Before production, install a UNIQUE index on event_key and implement
claim/lease, retry, delivery receipt, and dead-letter handling.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.extensions import mongo


class CafeBookingOutboxError(ValueError):
    """Invalid or unavailable booking outbox operation."""


_EVENT_TYPES = frozenset({
    "booking_created",
    "booking_confirmed",
    "booking_rejected",
    "booking_cancelled",
    "booking_completed",
    "booking_no_show",
})

_EVENT_STATUS = {
    "booking_created": "pending",
    "booking_confirmed": "confirmed",
    "booking_rejected": "rejected",
    "booking_cancelled": "cancelled",
    "booking_completed": "completed",
    "booking_no_show": "no_show",
}


def _object_id(value, label):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeBookingOutboxError(f"Invalid {label}.")
    return ObjectId(value)


def record_cafe_booking_event(
    *,
    reservation_id,
    venue_id,
    customer_id,
    event_type,
    reservation_status,
    session,
    occurred_at=None,
):
    """Persist one booking event inside its active write transaction.

    Each allowed event can occur at most once per reservation under
    the existing terminal-state lifecycle rules. The deterministic
    event_key must be backed by a unique MongoDB index before use.
    """
    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise CafeBookingOutboxError(
            "Active MongoDB transaction required."
        )

    if (
        not isinstance(event_type, str)
        or event_type not in _EVENT_TYPES
        or reservation_status != _EVENT_STATUS[event_type]
    ):
        raise CafeBookingOutboxError(
            "Invalid booking event or reservation status."
        )

    reservation_oid = _object_id(
        reservation_id, "reservation ID"
    )
    venue_oid = _object_id(venue_id, "venue ID")
    customer_oid = _object_id(customer_id, "customer ID")

    if occurred_at is None:
        timestamp = datetime.now(timezone.utc)
    elif (
        isinstance(occurred_at, datetime)
        and occurred_at.tzinfo is not None
        and occurred_at.utcoffset() is not None
    ):
        timestamp = occurred_at.astimezone(timezone.utc)
    else:
        raise CafeBookingOutboxError(
            "Event timestamp must include a timezone."
        )

    event_key = f"{reservation_oid}:{event_type}"

    document = {
        "event_key": event_key,
        "reservation_id": reservation_oid,
        "venue_id": venue_oid,
        "customer_id": customer_oid,
        "event_type": event_type,
        "reservation_status": reservation_status,
        "occurred_at": timestamp,
        "created_at": timestamp,
        "delivery_state": "pending",
        "attempt_count": 0,
        "next_attempt_at": timestamp,
        "locked_until": None,
        "last_error": None,
    }

    result = mongo.db.cafe_booking_outbox.insert_one(
        document,
        session=session,
    )

    inserted_id = getattr(result, "inserted_id", None)

    if not isinstance(inserted_id, ObjectId):
        raise CafeBookingOutboxError(
            "Booking event persistence acknowledgement failed."
        )

    return {
        "event_id": str(inserted_id),
        "event_key": event_key,
        "event_type": event_type,
        "delivery_state": "pending",
    }
