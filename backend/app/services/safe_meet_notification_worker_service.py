"""Safe Meet notification worker; sender supplied by caller.

At-least-once delivery. The provider may receive an event again
after an interrupted acknowledgement; deduplicate where supported.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.services.safe_meet_notification_delivery_service import (
    SafeMeetDeliveryError,
    acknowledge_notification,
    claim_due_notification,
    retry_notification,
)


class SafeMeetNotificationWorkerError(RuntimeError):
    pass


def _validate_item(item):
    required = (
        "idempotency_key",
        "recipient_id",
        "event_type",
        "safe_meet_id",
        "conversation_id",
    )
    for key in required:
        if type(item.get(key)) is not str or not item[key].strip():
            raise SafeMeetNotificationWorkerError(
                "Invalid outbox field: " + key
            )

    for key in ("recipient_id", "safe_meet_id", "conversation_id"):
        if not ObjectId.is_valid(item[key]):
            raise SafeMeetNotificationWorkerError(
                "Invalid identifier: " + key
            )

    if (
        item.get("delivery_status") != "processing"
        or type(item.get("revision")) is not int
        or item["revision"] < 1
        or not item.get("lease_token")
    ):
        raise SafeMeetNotificationWorkerError(
            "Invalid outbox revision or lease."
        )


def run_one_safe_meet_notification(
    *,
    database,
    worker_id,
    sender,
    now=None,
    lease_seconds=60,
    max_attempts=5,
):
    if not callable(sender):
        raise ValueError("A delivery sender is required.")

    if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
        raise ValueError("Invalid max_attempts.")

    moment = datetime.now(timezone.utc) if now is None else now

    item = claim_due_notification(
        database=database,
        worker_id=worker_id,
        now=moment,
        lease_seconds=lease_seconds,
    )
    if item is None:
        return {"outcome": "idle"}

    try:
        _validate_item(item)
        if sender(dict(item)) is not True:
            raise SafeMeetNotificationWorkerError(
                "Provider did not accept the notification."
            )
    except Exception as exc:
        error_code = (
            "INVALID_OUTBOX_RECORD"
            if isinstance(exc, SafeMeetNotificationWorkerError)
            and str(exc).startswith(("Invalid outbox", "Invalid identifier"))
            else "DELIVERY_NOT_ACCEPTED"
        )
        try:
            status = retry_notification(
                database=database,
                item_id=item["_id"],
                lease_token=item["lease_token"],
                now=moment,
                error_code=error_code,
                max_attempts=max_attempts,
            )
        except SafeMeetDeliveryError:
            status = "lease_lost"

        return {
            "outcome": status,
            "outbox_id": str(item["_id"]),
        }

    try:
        acknowledge_notification(
            database=database,
            item_id=item["_id"],
            lease_token=item["lease_token"],
            now=moment,
        )
    except SafeMeetDeliveryError:
        return {
            "outcome": "lease_lost",
            "outbox_id": str(item["_id"]),
        }

    return {"outcome": "sent", "outbox_id": str(item["_id"])}
