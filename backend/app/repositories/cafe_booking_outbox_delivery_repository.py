"""Atomic MongoDB leases for the Soultie cafe booking outbox.

This internal repository only claims durable events. It does not
send notifications, mark delivery successful, or acknowledge events.

Workers must later implement token-guarded delivery receipts, retry
scheduling, and idempotent downstream notification delivery.

An expired lease can be reclaimed. This means consumers must treat
delivery as at-least-once, not exactly-once.
"""

from datetime import datetime, timedelta, timezone
import secrets

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo


class CafeBookingDeliveryLeaseError(RuntimeError):
    """Booking outbox lease cannot be safely claimed."""


def _utc_now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeBookingDeliveryLeaseError(
            "A timezone-aware timestamp is required."
        )
    return value.astimezone(timezone.utc)


def _mongo_lease_matches(stored, expected):
    """Compare UTC BSON dates at millisecond precision."""
    if not isinstance(stored, datetime):
        return False
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    else:
        stored = stored.astimezone(timezone.utc)
    expected = expected.astimezone(timezone.utc)
    expected = expected.replace(
        microsecond=(expected.microsecond // 1000) * 1000
    )
    return stored == expected

def claim_next_cafe_booking_event(
    *, now=None, lease_seconds=90
):
    """Atomically claim one due event for a limited delivery lease.

    A returned claim must be acknowledged or retried by a separate
    token-guarded operation. Do not expose this to public HTTP callers.
    """
    timestamp = _utc_now(now)

    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 30 <= lease_seconds <= 900
    ):
        raise CafeBookingDeliveryLeaseError(
            "Invalid outbox delivery lease duration."
        )

    lease_until = timestamp + timedelta(seconds=lease_seconds)
    lease_token = secrets.token_hex(32)

    # Pending events must be due and not currently leased.
    # Processing events may only be recovered after lease expiry.
    eligible = {
        "$or": [
            {
                "delivery_state": "pending",
                "next_attempt_at": {"$lte": timestamp},
                "$or": [
                    {"locked_until": None},
                    {"locked_until": {"$lte": timestamp}},
                ],
            },
            {
                "delivery_state": "processing",
                "locked_until": {"$lte": timestamp},
            },
        ],
    }

    result = mongo.db.cafe_booking_outbox.find_one_and_update(
        eligible,
        {
            "$set": {
                "delivery_state": "processing",
                "locked_until": lease_until,
                "lease_token": lease_token,
                "claimed_at": timestamp,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[
            ("next_attempt_at", 1),
            ("created_at", 1),
            ("_id", 1),
        ],
        return_document=ReturnDocument.AFTER,
    )

    if result is None:
        return None

    if (
        not isinstance(result, dict)
        or not isinstance(result.get("_id"), ObjectId)
        or not isinstance(result.get("event_key"), str)
        or not result["event_key"]
        or not isinstance(result.get("reservation_id"), ObjectId)
        or not isinstance(result.get("venue_id"), ObjectId)
        or not isinstance(result.get("customer_id"), ObjectId)
        or not isinstance(result.get("event_type"), str)
        or result.get("delivery_state") != "processing"
        or result.get("lease_token") != lease_token
        or not _mongo_lease_matches(result.get("locked_until"), lease_until)
        or (
            type(result.get("attempt_count")) is not int
            or result["attempt_count"] < 1
        )
    ):
        raise CafeBookingDeliveryLeaseError(
            "Invalid outbox delivery lease acknowledgement."
        )

    return {
        "event_id": str(result["_id"]),
        "event_key": result["event_key"],
        "reservation_id": str(result["reservation_id"]),
        "venue_id": str(result["venue_id"]),
        "customer_id": str(result["customer_id"]),
        "event_type": result["event_type"],
        "lease_token": lease_token,
        "locked_until": lease_until,
        "attempt_count": result["attempt_count"],
    }
