"""Explicitly invoked, bounded exhausted-recipient maintenance.

Does not send notifications, register tasks, or run a scheduler.
The repository remains the atomic authority for every transition.
"""

from datetime import datetime, timezone

from app.extensions import mongo
from app.repositories.cafe_booking_recipient_exhausted_recovery_repository import (
    reconcile_exhausted_cafe_booking_recipient,
)
from app.services.cafe_booking_recipient_retry_policy_service import (
    MAX_ATTEMPTS,
)


class CafeRecipientMaintenanceError(ValueError):
    """Invalid maintenance request or database response."""


def maintain_exhausted_cafe_booking_recipients(
    *, enabled=False, max_records=10, now=None
):
    """Check at most max_records expired, exhausted delivery records.

    Concurrent maintenance callers may inspect the same record, but
    only the atomic repository operation may change its state.
    """
    if enabled is not True:
        return {
            "outcome": "disabled",
            "examined_count": 0,
            "reconciled_count": 0,
        }

    if type(max_records) is not int or not 1 <= max_records <= 100:
        raise CafeRecipientMaintenanceError(
            "max_records must be an integer between 1 and 100."
        )

    if now is None:
        timestamp = datetime.now(timezone.utc)
    elif (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeRecipientMaintenanceError(
            "Timezone-aware maintenance timestamp required."
        )
    else:
        timestamp = now.astimezone(timezone.utc)

    query = {
        "delivery_state": "processing",
        "attempt_count": {"$gte": MAX_ATTEMPTS},
        "locked_until": {"$type": "date", "$lte": timestamp},
    }

    cursor = (
        mongo.db.cafe_booking_recipient_deliveries
        .find(query, {"_id": 1})
        .sort([("locked_until", 1), ("_id", 1)])
        .limit(max_records)
    )

    examined = 0
    reconciled = 0

    for document in cursor:
        examined += 1

        if examined > max_records:
            raise CafeRecipientMaintenanceError(
                "Database cursor exceeded maintenance limit."
            )

        if (
            not isinstance(document, dict)
            or not isinstance(document.get("_id"), str)
        ):
            raise CafeRecipientMaintenanceError(
                "Invalid exhausted recipient database identity."
            )

        result = reconcile_exhausted_cafe_booking_recipient(
            delivery_key=document["_id"],
            now=timestamp,
        )

        if result is None:
            continue

        if (
            not isinstance(result, dict)
            or result.get("delivery_key") != document["_id"]
            or result.get("delivery_state") != "dead_letter"
            or result.get("recovery_reason") != "DELIVERY_LEASE_EXHAUSTED"
        ):
            raise CafeRecipientMaintenanceError(
                "Invalid exhausted recipient recovery acknowledgement."
            )

        reconciled += 1

    return {
        "outcome": "completed",
        "examined_count": examined,
        "reconciled_count": reconciled,
    }
