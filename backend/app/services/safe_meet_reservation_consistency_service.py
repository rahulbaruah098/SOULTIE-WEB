"""Check that a linked booking remains valid before meet consent.

Does not change reservations, create bookings, or persist Safe Meets.
A separate transaction-level guard is required for full race safety.
"""

from datetime import datetime, timezone

from bson import ObjectId
from app.extensions import mongo
from app.services.safe_meet_authorization_service import (
    SafeMeetAuthorizationError,
)


def _utc(value):
    if not isinstance(value, datetime):
        raise SafeMeetAuthorizationError(
            "Linked reservation time is invalid."
        )
    # Standard PyMongo BSON dates may be returned as naive UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def verify_linked_reservation_is_current(plan):
    """Fail closed if a linked booking no longer matches the plan."""
    if not isinstance(plan, dict):
        raise SafeMeetAuthorizationError("Safe Meet is unavailable.")

    reservation_id = plan.get("reservation_id")
    if reservation_id is None:
        return None

    try:
        reservation_oid = ObjectId(reservation_id)
        venue_oid = ObjectId(plan["venue_id"])
        owner_oid = ObjectId(plan["proposer_id"])
        planned = datetime.fromisoformat(plan["planned_at"])
        if planned.tzinfo is None:
            raise ValueError("Missing timezone.")
    except (KeyError, TypeError, ValueError) as exc:
        raise SafeMeetAuthorizationError(
            "Invalid linked reservation information."
        ) from exc

    record = mongo.db.reservations.find_one(
        {
            "_id": reservation_oid,
            "customer_id": owner_oid,
            "venue_id": venue_oid,
            "status": "confirmed",
        },
        {
            "_id": 1,
            "customer_id": 1,
            "venue_id": 1,
            "status": 1,
            "start_at": 1,
            "end_at": 1,
            "party_size": 1,
        },
    )

    if not isinstance(record, dict):
        raise SafeMeetAuthorizationError(
            "Linked reservation is no longer confirmed."
        )

    start_at = record.get("start_at")
    end_at = record.get("end_at")

    if (
        record.get("_id") != reservation_oid
        or record.get("customer_id") != owner_oid
        or record.get("venue_id") != venue_oid
        or record.get("status") != "confirmed"
        or type(record.get("party_size")) is not int
        or record["party_size"] < 2
        or (
            _utc(start_at).replace(
                microsecond=(
                    _utc(start_at).microsecond // 1000
                ) * 1000
            )
            != planned.astimezone(timezone.utc).replace(
                microsecond=(
                    planned.astimezone(timezone.utc).microsecond // 1000
                ) * 1000
            )
        )
        or _utc(end_at) <= _utc(start_at)
    ):
        raise SafeMeetAuthorizationError(
            "Linked reservation no longer matches this Safe Meet."
        )

    return record
