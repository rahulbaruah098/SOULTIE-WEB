"""Leased, resumable Safe Meet notification delivery state machine.

This service claims and acknowledges outbox items. It never calls FCM,
email or other external providers. The sending adapter is separate.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument


class SafeMeetDeliveryError(RuntimeError):
    pass


def _require_time(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required.")
    return now.astimezone(timezone.utc)


def _collection(database):
    if database is None:
        raise ValueError("MongoDB database is required.")
    return database.safe_meet_notification_outbox


def claim_due_notification(*, database, worker_id, now, lease_seconds=60):
    """Atomically claim a due item or take over an expired lease."""
    now = _require_time(now)
    if (
        not isinstance(worker_id, str)
        or not worker_id.strip()
        or len(worker_id) > 100
        or type(lease_seconds) is not int
        or not 10 <= lease_seconds <= 3600
    ):
        raise ValueError("Invalid worker or lease duration.")

    token = uuid4().hex
    lease_until = now + timedelta(seconds=lease_seconds)

    return _collection(database).find_one_and_update(
        {
            "$or": [
                {
                    "delivery_status": "pending",
                    "next_attempt_at": {"$lte": now},
                },
                {
                    "delivery_status": "processing",
                    "lease_until": {"$lte": now},
                },
            ]
        },
        {
            "$set": {
                "delivery_status": "processing",
                "worker_id": worker_id,
                "lease_token": token,
                "lease_until": lease_until,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("next_attempt_at", 1), ("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def acknowledge_notification(*, database, item_id, lease_token, now):
    """Mark sent only if this worker still holds the exact lease."""
    now = _require_time(now)
    if not isinstance(lease_token, str) or not lease_token:
        raise ValueError("A lease token is required.")

    result = _collection(database).update_one(
        {
            "_id": item_id,
            "delivery_status": "processing",
            "lease_token": lease_token,
            "lease_until": {"$gt": now},
        },
        {
            "$set": {
                "delivery_status": "sent",
                "sent_at": now,
                "updated_at": now,
                "last_error": None,
            },
            "$unset": {
                "worker_id": "",
                "lease_token": "",
                "lease_until": "",
            },
        },
    )
    if result.modified_count != 1:
        raise SafeMeetDeliveryError(
            "Notification lease expired or was reassigned."
        )
    return True


def retry_notification(
    *,
    database,
    item_id,
    lease_token,
    now,
    error_code,
    max_attempts=5,
):
    """Schedule bounded exponential retry; stop after max attempts."""
    now = _require_time(now)
    if (
        not isinstance(lease_token, str)
        or not lease_token
        or not isinstance(error_code, str)
        or not error_code
        or len(error_code) > 100
        or type(max_attempts) is not int
        or not 1 <= max_attempts <= 20
    ):
        raise ValueError("Invalid retry parameters.")

    coll = _collection(database)
    item = coll.find_one({
        "_id": item_id,
        "delivery_status": "processing",
        "lease_token": lease_token,
        "lease_until": {"$gt": now},
    })
    if item is None:
        raise SafeMeetDeliveryError(
            "Notification lease expired or was reassigned."
        )

    attempts = item.get("attempt_count")
    if type(attempts) is not int or attempts < 1:
        raise SafeMeetDeliveryError("Invalid attempt counter.")

    exhausted = attempts >= max_attempts
    delay_seconds = min(3600, 30 * (2 ** min(attempts - 1, 7)))

    updates = {
        "delivery_status": "failed" if exhausted else "pending",
        "last_error": error_code,
        "updated_at": now,
    }
    if not exhausted:
        updates["next_attempt_at"] = (
            now + timedelta(seconds=delay_seconds)
        )

    result = coll.update_one(
        {
            "_id": item_id,
            "delivery_status": "processing",
            "lease_token": lease_token,
            "lease_until": {"$gt": now},
            "attempt_count": attempts,
        },
        {
            "$set": updates,
            "$unset": {
                "worker_id": "",
                "lease_token": "",
                "lease_until": "",
            },
        },
    )
    if result.modified_count != 1:
        raise SafeMeetDeliveryError(
            "Notification was changed concurrently."
        )

    return "failed" if exhausted else "pending"
