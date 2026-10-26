"""Durable per-recipient records for Soultie cafe booking notifications.

This repository initializes one independent delivery record for each
verified recipient returned by the trusted booking recipient router.

The deterministic delivery_key is MongoDB's _id. MongoDB enforces
uniqueness of _id without an additional index.

Repeated initialization never resets an existing delivery state.

This module does not send messages, acknowledge delivery, or mark the
parent outbox event delivered. Per-recipient claim/receipt operations,
and parent completion checks, are separate required work.

If initialization fails partway through a group, a retry can safely
finish the remaining records using the same deterministic keys.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo


class CafeRecipientDeliveryError(ValueError):
    """Recipient delivery identity or persistence is invalid."""


_EVENTS = frozenset({
    "booking_created",
    "booking_confirmed",
    "booking_rejected",
    "booking_cancelled",
    "booking_completed",
    "booking_no_show",
})

_ROLES = frozenset({"customer", "cafe_partner"})


def _timestamp(now):
    if now is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeRecipientDeliveryError(
            "Timezone-aware timestamp required."
        )
    return now.astimezone(timezone.utc)


def _validate_routing(routing):
    if not isinstance(routing, dict):
        raise CafeRecipientDeliveryError(
            "Verified recipient routing is required."
        )

    event_key = routing.get("event_key")
    event_type = routing.get("event_type")
    recipients = routing.get("recipients")

    if (
        not isinstance(event_key, str)
        or event_type not in _EVENTS
        or not isinstance(recipients, list)
        or not 1 <= len(recipients) <= 2
    ):
        raise CafeRecipientDeliveryError(
            "Invalid recipient routing structure."
        )

    parts = event_key.split(":")
    if (
        len(parts) != 2
        or not ObjectId.is_valid(parts[0])
        or parts[1] != event_type
        or event_key != f"{ObjectId(parts[0])}:{event_type}"
    ):
        raise CafeRecipientDeliveryError(
            "Invalid booking event key."
        )

    seen = set()
    clean = []

    for item in recipients:
        if not isinstance(item, dict):
            raise CafeRecipientDeliveryError(
                "Invalid recipient."
            )

        role = item.get("recipient_type")
        identity = item.get("recipient_id")
        key = item.get("delivery_key")

        if (
            role not in _ROLES
            or not isinstance(identity, str)
            or not ObjectId.is_valid(identity)
        ):
            raise CafeRecipientDeliveryError(
                "Invalid recipient identity."
            )

        identity = str(ObjectId(identity))
        expected = f"{event_key}:{role}:{identity}"

        if (
            key != expected
            or role in seen
        ):
            raise CafeRecipientDeliveryError(
                "Duplicate or mismatched delivery identity."
            )

        seen.add(role)
        clean.append((role, identity, key))

    if event_type == "booking_created":
        if seen != {"cafe_partner"}:
            raise CafeRecipientDeliveryError(
                "Invalid booking creation audience."
            )
    elif seen != {"customer", "cafe_partner"}:
        raise CafeRecipientDeliveryError(
            "Incomplete lifecycle notification audience."
        )

    return event_key, event_type, clean


def ensure_cafe_booking_recipient_deliveries(*, routing, now=None):
    """Initialize exactly one independent record per verified recipient.

    Call only after server-side recipient routing. Never pass a
    client-supplied routing dictionary to this repository.
    """
    timestamp = _timestamp(now)

    # Validate every recipient before performing any write.
    event_key, event_type, recipients = _validate_routing(routing)

    collection = mongo.db.cafe_booking_recipient_deliveries
    prepared = []

    for role, identity, delivery_key in recipients:
        document = {
            "_id": delivery_key,
            "event_key": event_key,
            "event_type": event_type,
            "recipient_type": role,
            "recipient_id": ObjectId(identity),
            "delivery_state": "pending",
            "attempt_count": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
            "delivered_at": None,
        }

        try:
            result = collection.update_one(
                {"_id": delivery_key},
                {"$setOnInsert": document},
                upsert=True,
            )
        except DuplicateKeyError:
            # Concurrent initialization may have inserted this exact
            # _id. Read the persisted identity before accepting it.
            result = None

        if result is not None and not (
            result.acknowledged is True
            and (
                result.matched_count == 1
                or result.upserted_id == delivery_key
            )
        ):
            raise CafeRecipientDeliveryError(
                "Recipient record persistence was not acknowledged."
            )

        stored = collection.find_one(
            {"_id": delivery_key},
            {
                "_id": 1,
                "event_key": 1,
                "event_type": 1,
                "recipient_type": 1,
                "recipient_id": 1,
                "delivery_state": 1,
            },
        )

        if (
            not isinstance(stored, dict)
            or stored.get("_id") != delivery_key
            or stored.get("event_key") != event_key
            or stored.get("event_type") != event_type
            or stored.get("recipient_type") != role
            or stored.get("recipient_id") != ObjectId(identity)
            or stored.get("delivery_state")
            not in {
                "pending", "processing",
                "delivered", "dead_letter",
            }
        ):
            raise CafeRecipientDeliveryError(
                "Persisted recipient record does not match routing."
            )

        prepared.append({
            "delivery_key": delivery_key,
            "recipient_type": role,
            "recipient_id": identity,
            "delivery_state": stored["delivery_state"],
        })

    return {
        "event_key": event_key,
        "event_type": event_type,
        "recipients": prepared,
        "all_delivered": all(
            r["delivery_state"] == "delivered"
            for r in prepared
        ),
    }
