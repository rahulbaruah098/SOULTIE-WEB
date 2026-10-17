"""Verified parent completion for Xuoroni cafe notification outbox.

Never acknowledge a parent booking event as delivered merely because
one recipient or one external provider returned success.

The caller must supply a trusted server-resolved routing result and
the currently claimed parent event. This service checks every
required, independently persisted recipient receipt before it
acknowledges the parent's active delivery lease.

No notification is sent here. No live worker is started here.

This implementation relies on delivered recipient states being
terminal and immutable. Live MongoDB race and crash tests remain
required before production.
"""

from bson import ObjectId

from app.extensions import mongo
from app.repositories.cafe_booking_recipient_delivery_repository import (
    _validate_routing,
    CafeRecipientDeliveryError,
)
from app.repositories.cafe_booking_outbox_receipt_repository import (
    acknowledge_cafe_booking_delivery,
)


class CafeBookingOutboxCompletionError(ValueError):
    """Parent booking delivery cannot be verified."""


def complete_cafe_booking_outbox_if_ready(*, claim, routing):
    """Acknowledge the parent only after all required receipts exist.

    Pending, processing, missing or dead-lettered recipients cause
    an incomplete result, never a success receipt. A higher-level
    worker must separately decide whether to retry or escalate.
    """
    if not isinstance(claim, dict):
        raise CafeBookingOutboxCompletionError(
            "A trusted parent outbox claim is required."
        )

    try:
        event_key, event_type, recipients = _validate_routing(
            routing
        )
    except CafeRecipientDeliveryError as exc:
        raise CafeBookingOutboxCompletionError(
            "Invalid recipient routing contract."
        ) from exc

    event_id = claim.get("event_id")
    lease_token = claim.get("lease_token")
    attempt_count = claim.get("attempt_count")

    if (
        not isinstance(event_id, str)
        or not ObjectId.is_valid(event_id)
        or claim.get("event_key") != event_key
        or claim.get("event_type") != event_type
        or not isinstance(lease_token, str)
        or len(lease_token) != 64
        or any(char not in "0123456789abcdef" for char in lease_token)
        or type(attempt_count) is not int
        or attempt_count < 1
    ):
        raise CafeBookingOutboxCompletionError(
            "Invalid or mismatched parent delivery claim."
        )

    reservation_id = str(ObjectId(event_key.split(":")[0]))
    if claim.get("reservation_id") != reservation_id:
        raise CafeBookingOutboxCompletionError(
            "Parent reservation identity mismatch."
        )

    collection = mongo.db.cafe_booking_recipient_deliveries
    pending = []
    blocked = []

    for role, identity, delivery_key in recipients:
        stored = collection.find_one(
            {"_id": delivery_key},
            {
                "_id": 1,
                "event_key": 1,
                "event_type": 1,
                "recipient_type": 1,
                "recipient_id": 1,
                "delivery_state": 1,
                "delivered_at": 1,
            },
        )

        if stored is None:
            pending.append(role)
            continue

        if (
            not isinstance(stored, dict)
            or stored.get("_id") != delivery_key
            or stored.get("event_key") != event_key
            or stored.get("event_type") != event_type
            or stored.get("recipient_type") != role
            or stored.get("recipient_id") != ObjectId(identity)
        ):
            raise CafeBookingOutboxCompletionError(
                "Persisted recipient identity mismatch."
            )

        state = stored.get("delivery_state")

        if state == "delivered":
            if stored.get("delivered_at") is None:
                raise CafeBookingOutboxCompletionError(
                    "Recipient delivery receipt is incomplete."
                )
        elif state in {"pending", "processing"}:
            pending.append(role)
        elif state == "dead_letter":
            blocked.append(role)
        else:
            raise CafeBookingOutboxCompletionError(
                "Invalid persisted recipient delivery state."
            )

    if pending or blocked:
        return {
            "event_id": event_id,
            "outcome": "blocked" if blocked else "incomplete",
            "all_delivered": False,
            "pending_count": len(pending),
            "dead_letter_count": len(blocked),
        }

    acknowledged = acknowledge_cafe_booking_delivery(
        event_id=event_id,
        lease_token=lease_token,
        attempt_count=attempt_count,
    )

    if (
        not isinstance(acknowledged, dict)
        or acknowledged.get("event_id") != event_id
        or acknowledged.get("delivery_state") != "delivered"
    ):
        raise CafeBookingOutboxCompletionError(
            "Invalid parent delivery acknowledgement."
        )

    return {
        "event_id": event_id,
        "outcome": "delivered",
        "all_delivered": True,
        "pending_count": 0,
        "dead_letter_count": 0,
    }
