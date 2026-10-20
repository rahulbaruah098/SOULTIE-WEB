"""Transaction-bound reservation snapshot for opening-hours edits.

The caller MUST acquire the existing venue reservation write fence
within the SAME MongoDB transaction before invoking this function.

This repository only reads. It does not acquire the fence, save
opening hours, create bookings or guarantee atomicity by itself.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.extensions import mongo
from app.schemas.reservation import ACTIVE_RESERVATION_STATUSES


MAX_HOURS_RESERVATIONS = 500


class VenueHoursSnapshotError(ValueError):
    """The reservation snapshot cannot safely authorize an hours edit."""


def _utc(value, label):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise VenueHoursSnapshotError(
            f"Invalid timezone-aware {label}."
        )
    return value.astimezone(timezone.utc)


def _mongo_utc(value, label):
    """Decode MongoDB BSON datetimes as UTC.

    PyMongo's default tz_aware=False decodes BSON UTC timestamps as
    naive Python datetimes. This conversion applies only to database
    result values, never to caller-provided timestamps.
    """
    if not isinstance(value, datetime):
        raise VenueHoursSnapshotError(
            f"Invalid MongoDB {label}."
        )

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    if value.utcoffset() is None:
        raise VenueHoursSnapshotError(
            f"Invalid MongoDB {label}."
        )

    return value.astimezone(timezone.utc)


def read_active_hours_reservation_intervals(
    *,
    venue_id,
    session,
    now=None,
):
    """Read every ongoing or future active reservation for one venue.

    Returns normalized interval objects accepted by the opening-hours
    guard. Historical completed intervals are excluded using end_at,
    while missing or null end_at values remain included and cause
    validation failure rather than being silently skipped.
    """
    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise VenueHoursSnapshotError(
            "An active MongoDB transaction is required."
        )

    if (
        not isinstance(venue_id, str)
        or len(venue_id) != 24
        or not ObjectId.is_valid(venue_id)
    ):
        raise VenueHoursSnapshotError("Invalid venue ID.")

    moment = _utc(
        datetime.now(timezone.utc) if now is None else now,
        "snapshot time",
    )

    venue_oid = ObjectId(venue_id)

    query = {
        "venue_id": venue_oid,
        "status": {
            "$in": sorted(ACTIVE_RESERVATION_STATUSES),
        },
        "$or": [
            {"end_at": {"$gt": moment}},
            {"end_at": {"$exists": False}},
            {"end_at": None},
        ],
    }

    projection = {
        "_id": 1,
        "venue_id": 1,
        "status": 1,
        "start_at": 1,
        "end_at": 1,
    }

    cursor = (
        mongo.db.reservations.find(
            query,
            projection,
            session=session,
        )
        .sort([("start_at", 1), ("_id", 1)])
        .limit(MAX_HOURS_RESERVATIONS + 1)
    )

    rows = list(cursor)

    if len(rows) > MAX_HOURS_RESERVATIONS:
        raise VenueHoursSnapshotError(
            "Too many active reservations to safely edit hours."
        )

    intervals = []

    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("_id"), ObjectId)
            or row.get("venue_id") != venue_oid
            or row.get("status") not in ACTIVE_RESERVATION_STATUSES
        ):
            raise VenueHoursSnapshotError(
                "Invalid reservation snapshot record."
            )

        start = _mongo_utc(row.get("start_at"), "reservation start")
        end = _mongo_utc(row.get("end_at"), "reservation end")

        if (
            start >= end
            or end <= moment
        ):
            raise VenueHoursSnapshotError(
                "Contradictory reservation snapshot interval."
            )

        intervals.append({
            "start_at": start,
            "end_at": end,
        })

    return intervals
