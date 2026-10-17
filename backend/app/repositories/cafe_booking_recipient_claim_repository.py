"""Atomic delivery claims for individual Xuoroni cafe recipients.

Each claim targets one deterministic, already initialized recipient
delivery record. A successful claim creates a worker-specific lease
token and increments the individual recipient attempt count.

Pending records are claimable when due. Expired processing leases
may be recovered. Delivered and dead-letter records are excluded.

A separate receipt repository must verify the claim token, attempt
number and active lease before acknowledging delivery or failure.

This repository never sends notifications or marks delivery complete.
External notification systems require idempotency by delivery_key.
"""

from datetime import datetime, timedelta, timezone
import secrets

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo


class CafeRecipientClaimError(ValueError):
    """Individual notification delivery cannot be safely claimed."""


def _timestamp(now):
    if now is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeRecipientClaimError(
            "Timezone-aware claim timestamp required."
        )
    return now.astimezone(timezone.utc)


def _delivery_identity(delivery_key):
    if not isinstance(delivery_key, str):
        raise CafeRecipientClaimError("Invalid delivery identity.")

    parts = delivery_key.split(":")

    if (
        len(parts) != 4
        or not ObjectId.is_valid(parts[0])
        or parts[1] not in {
            "booking_created",
            "booking_confirmed",
            "booking_rejected",
            "booking_cancelled",
            "booking_completed",
            "booking_no_show",
        }
        or parts[2] not in {"customer", "cafe_partner"}
        or not ObjectId.is_valid(parts[3])
        or parts[0] != str(ObjectId(parts[0]))
        or parts[3] != str(ObjectId(parts[3]))
    ):
        raise CafeRecipientClaimError(
            "Invalid recipient delivery key."
        )

    return {
        "delivery_key": delivery_key,
        "event_key": f"{parts[0]}:{parts[1]}",
        "event_type": parts[1],
        "recipient_type": parts[2],
        "recipient_id": ObjectId(parts[3]),
    }


def _mongo_lease_matches(stored, expected):
    """Compare a MongoDB UTC timestamp at BSON millisecond precision."""
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

def claim_cafe_booking_recipient_delivery(
    *, delivery_key, now=None, lease_seconds=90
):
    """Atomically lease one specific recipient's due notification.

    Call only with the trusted delivery_key from the verified
    recipient routing and initialization pipeline.
    """
    timestamp = _timestamp(now)
    identity = _delivery_identity(delivery_key)

    if (
        type(lease_seconds) is not int
        or not 30 <= lease_seconds <= 900
    ):
        raise CafeRecipientClaimError(
            "Invalid recipient delivery lease duration."
        )

    lease_until = timestamp + timedelta(
        seconds=lease_seconds
    )
    lease_token = secrets.token_hex(32)

    eligible = {
        "_id": identity["delivery_key"],
        "event_key": identity["event_key"],
        "event_type": identity["event_type"],
        "recipient_type": identity["recipient_type"],
        "recipient_id": identity["recipient_id"],
        "$or": [
            {
                "delivery_state": "pending",
                "$and": [
                    {
                        "$or": [
                            {"next_attempt_at": {"$exists": False}},
                            {"next_attempt_at": {"$lte": timestamp}},
                        ]
                    },
                    {
                        "$or": [
                            {"locked_until": None},
                            {"locked_until": {"$lte": timestamp}},
                        ]
                    },
                ],
            },
            {
                "delivery_state": "processing",
                "locked_until": {"$lte": timestamp},
            },
        ],
    }

    updated = mongo.db.cafe_booking_recipient_deliveries.find_one_and_update(
        eligible,
        {
            "$set": {
                "delivery_state": "processing",
                "lease_token": lease_token,
                "locked_until": lease_until,
                "claimed_at": timestamp,
                "updated_at": timestamp,
            },
            "$inc": {"attempt_count": 1},
        },
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        return None

    if (
        not isinstance(updated, dict)
        or updated.get("_id") != identity["delivery_key"]
        or updated.get("event_key") != identity["event_key"]
        or updated.get("event_type") != identity["event_type"]
        or updated.get("recipient_type") != identity["recipient_type"]
        or updated.get("recipient_id") != identity["recipient_id"]
        or updated.get("delivery_state") != "processing"
        or updated.get("lease_token") != lease_token
        or not _mongo_lease_matches(updated.get("locked_until"), lease_until)
        or type(updated.get("attempt_count")) is not int
        or updated["attempt_count"] < 1
    ):
        raise CafeRecipientClaimError(
            "Invalid recipient delivery claim acknowledgement."
        )

    return {
        "delivery_key": identity["delivery_key"],
        "event_key": identity["event_key"],
        "event_type": identity["event_type"],
        "recipient_type": identity["recipient_type"],
        "recipient_id": str(identity["recipient_id"]),
        "lease_token": lease_token,
        "locked_until": lease_until,
        "attempt_count": updated["attempt_count"],
    }
