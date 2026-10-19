"""Token-guarded receipts for individual cafe booking recipients.

Only a worker holding the current, unexpired recipient-specific lease
may record delivery success or failure.

Each receipt uses atomic compare-and-set on the deterministic delivery
key, lease token, attempt counter, and lease expiration.

This repository does not mark the parent booking event delivered.
That requires a separate verified all-recipients-complete check.

External providers are at-least-once, not exactly-once. Actual
delivery integrations must deduplicate by delivery_key.
"""

from datetime import datetime, timedelta, timezone
import re

from app.services.cafe_booking_recipient_retry_policy_service import (
    calculate_cafe_recipient_retry,
    MAX_ATTEMPTS as MAX_RECIPIENT_ATTEMPTS,
    BASE_DELAY_SECONDS as RETRY_BASE_SECONDS,
    MAX_DELAY_SECONDS as RETRY_MAX_SECONDS,
)

from app.extensions import mongo
from app.repositories.cafe_booking_recipient_claim_repository import (
    _delivery_identity,
    CafeRecipientClaimError,
)




class CafeRecipientReceiptError(ValueError):
    """Recipient acknowledgement input is invalid."""


class CafeRecipientReceiptConflict(CafeRecipientReceiptError):
    """The worker no longer holds this recipient's active lease."""


def _timestamp(value):
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeRecipientReceiptError(
            "Timezone-aware delivery receipt timestamp required."
        )
    return value.astimezone(timezone.utc)


def _query(delivery_key, lease_token, attempt_count, timestamp):
    try:
        identity = _delivery_identity(delivery_key)
    except CafeRecipientClaimError as exc:
        raise CafeRecipientReceiptError(
            "Invalid recipient delivery identity."
        ) from exc

    if (
        not isinstance(lease_token, str)
        or re.fullmatch(r"[0-9a-f]{64}", lease_token) is None
    ):
        raise CafeRecipientReceiptError(
            "Invalid recipient delivery lease token."
        )

    if type(attempt_count) is not int or attempt_count < 1:
        raise CafeRecipientReceiptError(
            "Invalid recipient delivery attempt count."
        )

    return {
        "_id": identity["delivery_key"],
        "event_key": identity["event_key"],
        "event_type": identity["event_type"],
        "recipient_type": identity["recipient_type"],
        "recipient_id": identity["recipient_id"],
        "delivery_state": "processing",
        "lease_token": lease_token,
        "attempt_count": attempt_count,
        "locked_until": {"$gt": timestamp},
    }


def _write(query, update):
    result = mongo.db.cafe_booking_recipient_deliveries.update_one(
        query, update
    )

    if (
        result is None
        or result.matched_count != 1
        or result.modified_count != 1
    ):
        raise CafeRecipientReceiptConflict(
            "Recipient delivery lease is expired or replaced."
        )


def acknowledge_cafe_booking_recipient_delivery(
    *,
    delivery_key,
    lease_token,
    attempt_count,
    now=None,
):
    """Acknowledge one recipient only with its current active lease."""
    timestamp = _timestamp(now)
    query = _query(
        delivery_key, lease_token, attempt_count, timestamp
    )

    update = {
        "$set": {
            "delivery_state": "delivered",
            "delivered_at": timestamp,
            "updated_at": timestamp,
            "locked_until": None,
            "next_attempt_at": None,
            "last_error": None,
        },
        "$unset": {
            "lease_token": "",
            "claimed_at": "",
        },
    }

    _write(query, update)

    return {
        "delivery_key": delivery_key,
        "delivery_state": "delivered",
    }


def record_cafe_booking_recipient_failure(
    *,
    delivery_key,
    lease_token,
    attempt_count,
    failure_code,
    now=None,
):
    """Retry or dead-letter one recipient; never affect other recipients.

    Store only a short, safe category; never persist raw provider
    responses, contact information, or notification message contents.
    """
    timestamp = _timestamp(now)
    query = _query(
        delivery_key, lease_token, attempt_count, timestamp
    )

    if (
        not isinstance(failure_code, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", failure_code)
        is None
    ):
        raise CafeRecipientReceiptError(
            "Invalid recipient delivery failure category."
        )

    decision = calculate_cafe_recipient_retry(
        attempt_count=attempt_count,
        now=timestamp,
    )
    dead_letter = decision['action'] == 'dead_letter'

    fields = {
        "delivery_state": (
            "dead_letter" if dead_letter else "pending"
        ),
        "last_error": failure_code,
        "last_failed_at": timestamp,
        "updated_at": timestamp,
        "locked_until": None,
    }

    if dead_letter:
        fields["dead_lettered_at"] = timestamp
        fields["next_attempt_at"] = None
    else:
        fields["next_attempt_at"] = decision['retry_after']

    update = {
        "$set": fields,
        "$unset": {
            "lease_token": "",
            "claimed_at": "",
        },
    }

    _write(query, update)

    return {
        "delivery_key": delivery_key,
        "delivery_state": fields["delivery_state"],
        "next_attempt_at": fields["next_attempt_at"],
        "attempt_count": attempt_count,
    }
