"""Lease-token guarded delivery receipts for Soultie cafe booking events.

Only the worker holding a currently valid processing lease may
acknowledge delivery or schedule a failure retry.

All mutations are atomic compare-and-set updates. A stale worker
cannot update a reclaimed event.

Delivery is at-least-once. Downstream consumers must additionally
deduplicate by event_key. No external notification is sent here.
"""

from datetime import datetime, timedelta, timezone
import re

from bson import ObjectId


from app.extensions import mongo


MAX_DELIVERY_ATTEMPTS = 8
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 3600


class CafeBookingReceiptError(ValueError):
    """A delivery acknowledgement is invalid or stale."""


class CafeBookingReceiptConflict(CafeBookingReceiptError):
    """The outbox lease is no longer held by this worker."""


def _now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeBookingReceiptError(
            "Timezone-aware acknowledgement time required."
        )
    return value.astimezone(timezone.utc)


def _claim(event_id, lease_token, attempt_count, now):
    if (
        not isinstance(event_id, str)
        or not ObjectId.is_valid(event_id)
    ):
        raise CafeBookingReceiptError("Invalid outbox event ID.")

    if (
        not isinstance(lease_token, str)
        or re.fullmatch(r"[0-9a-f]{64}", lease_token) is None
    ):
        raise CafeBookingReceiptError("Invalid delivery lease token.")

    if (
        type(attempt_count) is not int
        or attempt_count < 1
    ):
        raise CafeBookingReceiptError("Invalid delivery attempt.")

    return {
        "_id": ObjectId(event_id),
        "delivery_state": "processing",
        "lease_token": lease_token,
        "attempt_count": attempt_count,
        "locked_until": {"$gt": now},
    }


def _acknowledge(*, query, update):
    result = mongo.db.cafe_booking_outbox.update_one(
        query, update
    )

    if (
        result is None
        or result.matched_count != 1
        or result.modified_count != 1
    ):
        raise CafeBookingReceiptConflict(
            "Booking notification delivery lease is unavailable."
        )


def acknowledge_cafe_booking_delivery(
    *,
    event_id,
    lease_token,
    attempt_count,
    now=None,
):
    """Mark a notification delivered only with its active lease."""
    timestamp = _now(now)
    query = _claim(
        event_id, lease_token, attempt_count, timestamp
    )

    update = {
        "$set": {
            "delivery_state": "delivered",
            "delivered_at": timestamp,
            "locked_until": None,
            "last_error": None,
        },
        "$unset": {
            "lease_token": "",
            "claimed_at": "",
        },
    }

    _acknowledge(query=query, update=update)

    return {
        "event_id": event_id,
        "delivery_state": "delivered",
    }


def record_cafe_booking_delivery_failure(
    *,
    event_id,
    lease_token,
    attempt_count,
    failure_code,
    now=None,
):
    """Release a failed event for retry or move it to dead-letter.

    failure_code must be a short machine-readable category, never a
    raw provider response, message body, credential or personal data.
    """
    timestamp = _now(now)
    query = _claim(
        event_id, lease_token, attempt_count, timestamp
    )

    if (
        not isinstance(failure_code, str)
        or re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,63}", failure_code
        ) is None
    ):
        raise CafeBookingReceiptError(
            "Invalid notification failure code."
        )

    terminal = attempt_count >= MAX_DELIVERY_ATTEMPTS

    update_fields = {
        "delivery_state": (
            "dead_letter" if terminal else "pending"
        ),
        "locked_until": None,
        "last_error": failure_code,
        "last_failed_at": timestamp,
    }

    if terminal:
        update_fields["dead_lettered_at"] = timestamp
        update_fields["next_attempt_at"] = None
    else:
        delay = min(
            RETRY_BASE_SECONDS * (2 ** (attempt_count - 1)),
            RETRY_MAX_SECONDS,
        )
        update_fields["next_attempt_at"] = (
            timestamp + timedelta(seconds=delay)
        )

    update = {
        "$set": update_fields,
        "$unset": {
            "lease_token": "",
            "claimed_at": "",
        },
    }

    _acknowledge(query=query, update=update)

    return {
        "event_id": event_id,
        "delivery_state": update_fields["delivery_state"],
        "next_attempt_at": update_fields["next_attempt_at"],
        "attempt_count": attempt_count,
    }
