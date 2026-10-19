"""Atomic reconciliation of an exhausted cafe recipient delivery lease.

This module NEVER sends notifications or acquires a new delivery lease.
An expired eighth-or-later processing claim is terminally reconciled.
No scheduler or production entry point is enabled here.
"""

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.extensions import mongo
from app.repositories.cafe_booking_recipient_claim_repository import (
    _delivery_identity,
)
from app.services.cafe_booking_recipient_retry_policy_service import (
    MAX_ATTEMPTS,
)


class CafeRecipientExhaustedRecoveryError(ValueError):
    """Invalid exhausted-recipient recovery request."""


def reconcile_exhausted_cafe_booking_recipient(
    *, delivery_key, now=None
):
    """Atomically dead-letter only an expired, exhausted processing lease.

    Return None when no document currently qualifies. A valid
    unexpired attempt must be allowed to finish its own receipt.
    """
    identity = _delivery_identity(delivery_key)

    if now is None:
        timestamp = datetime.now(timezone.utc)
    elif (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeRecipientExhaustedRecoveryError(
            "Timezone-aware recovery timestamp required."
        )
    else:
        timestamp = now.astimezone(timezone.utc)

    query = {
        "_id": identity["delivery_key"],
        "event_key": identity["event_key"],
        "event_type": identity["event_type"],
        "recipient_type": identity["recipient_type"],
        "recipient_id": identity["recipient_id"],
        "delivery_state": "processing",
        "attempt_count": {"$gte": MAX_ATTEMPTS},
        "locked_until": {
            "$type": "date",
            "$lte": timestamp,
        },
    }

    update = {
        "$set": {
            "delivery_state": "dead_letter",
            "dead_lettered_at": timestamp,
            "updated_at": timestamp,
            "locked_until": None,
            "next_attempt_at": None,
            "last_error": "DELIVERY_LEASE_EXHAUSTED",
        },
        "$unset": {
            "lease_token": "",
            "claimed_at": "",
        },
    }

    recovered = (
        mongo.db.cafe_booking_recipient_deliveries.find_one_and_update(
            query,
            update,
            return_document=ReturnDocument.AFTER,
        )
    )

    if recovered is None:
        return None

    if (
        not isinstance(recovered, dict)
        or recovered.get("_id") != identity["delivery_key"]
        or recovered.get("delivery_state") != "dead_letter"
        or type(recovered.get("attempt_count")) is not int
        or recovered["attempt_count"] < MAX_ATTEMPTS
        or recovered.get("next_attempt_at") is not None
    ):
        raise CafeRecipientExhaustedRecoveryError(
            "Exhausted recipient reconciliation acknowledgement invalid."
        )

    return {
        "delivery_key": identity["delivery_key"],
        "delivery_state": "dead_letter",
        "attempt_count": recovered["attempt_count"],
        "recovery_reason": "DELIVERY_LEASE_EXHAUSTED",
    }
