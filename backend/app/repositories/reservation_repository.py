"""Read-only venue-scoped reservation repository.

Provides a bounded lookup of pending and confirmed bookings
whose start time falls inside a UTC window.

This is not a complete interval-overlap or capacity check.
Callers must NOT treat an empty result as proof that a new
booking is available. No booking creation or mutation occurs.
"""

from datetime import datetime, timedelta, timezone

from bson import ObjectId

from app.extensions import mongo
from app.schemas.reservation import ACTIVE_RESERVATION_STATUSES


class ReservationRepositoryError(ValueError):
    """Invalid or untrustworthy reservation lookup."""


MAX_RESERVATIONS_PER_WINDOW = 500


def _object_id(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise ReservationRepositoryError(
            "Invalid reservation venue ID."
        )
    return ObjectId(value)


def _utc_time(value, *, label):
    if not isinstance(value, datetime):
        raise ReservationRepositoryError(
            f"{label} must be a datetime."
        )
    # PyMongo returns BSON UTC dates as naive UTC by default.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value.utcoffset() is None:
        raise ReservationRepositoryError(
            f"{label} must have a valid timezone."
        )
    return value.astimezone(timezone.utc)


def find_active_reservation_starts(
    *, venue_id, start_at, end_at
):
    """Read a bounded set of pending/confirmed reservation starts.

    Uses a half-open [start_at, end_at) window. An extra
    document detects result overflow and fails closed.

    This query cannot detect bookings that start before the
    requested window but overlap it. A separate interval-aware
    capacity engine is required before enabling booking writes.
    """
    venue_oid = _object_id(venue_id)
    beginning = _utc_time(start_at, label="start_at")
    ending = _utc_time(end_at, label="end_at")

    if beginning >= ending:
        raise ReservationRepositoryError(
            "Reservation query window is invalid."
        )

    # Limit oversized windows so accidental broad queries do not
    # become unbounded scans, even when a collection index exists.
    if (ending - beginning).total_seconds() > 366 * 24 * 3600:
        raise ReservationRepositoryError(
            "Reservation query window is too large."
        )

    query = {
        "venue_id": venue_oid,
        "status": {
            "$in": sorted(ACTIVE_RESERVATION_STATUSES)
        },
        "start_at": {
            "$gte": beginning,
            "$lt": ending,
        },
    }

    cursor = (
        mongo.db.reservations.find(query)
        .sort([("start_at", 1), ("_id", 1)])
        .limit(MAX_RESERVATIONS_PER_WINDOW + 1)
    )
    rows = list(cursor)

    if len(rows) > MAX_RESERVATIONS_PER_WINDOW:
        raise ReservationRepositoryError(
            "Reservation lookup exceeds safe result limit."
        )

    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("venue_id") != venue_oid
            or row.get("status") not in ACTIVE_RESERVATION_STATUSES
        ):
            raise ReservationRepositoryError(
                "Reservation lookup returned invalid records."
            )

        actual = _utc_time(
            row.get("start_at"),
            label="reservation start_at",
        )
        if not beginning <= actual < ending:
            raise ReservationRepositoryError(
                "Reservation lookup returned out-of-range records."
            )

    return rows


def insert_pending_cafe_reservation(
    *,
    customer_id,
    request_data,
    duration_minutes,
    buffer_minutes,
    session,
    now=None,
):
    """Insert one pending reservation inside an active transaction.

    This is persistence only. The caller MUST first authenticate the
    customer, acquire the venue write fence, read every potentially
    overlapping reservation with the SAME session, evaluate complete
    capacity, and resolve duplicate idempotency requests.

    The caller MUST NOT use this method as a standalone booking API.
    Duplicate-key errors deliberately propagate for the transaction
    coordinator to resolve after aborting the failed transaction.
    """
    from app.schemas.reservation import validate_reservation_request

    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise ReservationRepositoryError(
            "An active reservation transaction is required."
        )

    customer_oid = _object_id(customer_id)
    cleaned = validate_reservation_request(request_data)
    venue_oid = _object_id(cleaned["venue_id"])

    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or not 15 <= duration_minutes <= 720
    ):
        raise ReservationRepositoryError(
            "Invalid reservation duration."
        )

    if (
        isinstance(buffer_minutes, bool)
        or not isinstance(buffer_minutes, int)
        or not 0 <= buffer_minutes <= 180
    ):
        raise ReservationRepositoryError(
            "Invalid reservation buffer."
        )

    if now is None:
        created_at = datetime.now(timezone.utc)
    else:
        created_at = _utc_time(now, label="now")

    document = {
        "venue_id": venue_oid,
        "customer_id": customer_oid,
        "start_at": cleaned["start_at"],
        "end_at": cleaned["start_at"] + timedelta(
            minutes=duration_minutes + buffer_minutes
        ),
        "party_size": cleaned["party_size"],
        "duration_minutes": duration_minutes,
        "buffer_minutes": buffer_minutes,
        "status": "pending",
        "idempotency_key": cleaned["idempotency_key"],
        "notes": cleaned["notes"],
        "created_at": created_at,
        "updated_at": created_at,
    }

    result = mongo.db.reservations.insert_one(
        document,
        session=session,
    )

    reservation_id = getattr(result, "inserted_id", None)

    if not isinstance(reservation_id, ObjectId):
        raise ReservationRepositoryError(
            "Reservation insert acknowledgement is invalid."
        )

    return {
        "reservation_id": str(reservation_id),
        "venue_id": cleaned["venue_id"],
        "status": "pending",
        "start_at": cleaned["start_at"],
        "end_at": document["end_at"],
        "party_size": cleaned["party_size"],
    }


def find_overlapping_active_reservations(
    *, venue_id, start_at, end_at, session
):
    """Read all potentially overlapping active reservations in a transaction.

    The caller must acquire the SAME venue reservation write fence
    earlier in this transaction. All concurrent booking writers
    must honor that fence.

    Missing end_at values are explicitly included by the query
    and rejected: legacy records cannot silently appear available.
    The query deliberately scans starts earlier than the proposed
    end, not only starts inside the requested interval.

    This function alone does not reserve or guarantee capacity.
    """
    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise ReservationRepositoryError(
            "An active reservation transaction is required."
        )

    venue_oid = _object_id(venue_id)
    beginning = _utc_time(start_at, label="start_at")
    ending = _utc_time(end_at, label="end_at")

    if beginning >= ending:
        raise ReservationRepositoryError(
            "Reservation overlap window is invalid."
        )

    if (ending - beginning).total_seconds() > 366 * 24 * 3600:
        raise ReservationRepositoryError(
            "Reservation overlap window is too large."
        )

    query = {
        "venue_id": venue_oid,
        "status": {
            "$in": sorted(ACTIVE_RESERVATION_STATUSES)
        },
        "start_at": {"$lt": ending},
        "$or": [
            {"end_at": {"$gt": beginning}},
            {"end_at": {"$exists": False}},
            {"end_at": None},
        ],
    }

    cursor = (
        mongo.db.reservations.find(query, session=session)
        .sort([("start_at", 1), ("_id", 1)])
        .limit(MAX_RESERVATIONS_PER_WINDOW + 1)
    )
    rows = list(cursor)

    if len(rows) > MAX_RESERVATIONS_PER_WINDOW:
        raise ReservationRepositoryError(
            "Reservation overlap result limit exceeded."
        )

    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("venue_id") != venue_oid
            or row.get("status") not in ACTIVE_RESERVATION_STATUSES
        ):
            raise ReservationRepositoryError(
                "Invalid overlapping reservation record."
            )

        existing_start = _utc_time(
            row.get("start_at"), label="existing start_at"
        )
        existing_end = _utc_time(
            row.get("end_at"), label="existing end_at"
        )

        if (
            existing_start >= existing_end
            or existing_start >= ending
            or existing_end <= beginning
        ):
            raise ReservationRepositoryError(
                "Invalid reservation overlap acknowledgement."
            )

        party = row.get("party_size")
        duration = row.get("duration_minutes")
        buffer = row.get("buffer_minutes")

        if (
            isinstance(party, bool)
            or not isinstance(party, int)
            or not 1 <= party <= 100
            or isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 15 <= duration <= 720
            or isinstance(buffer, bool)
            or not isinstance(buffer, int)
            or not 0 <= buffer <= 180
            or existing_end != existing_start + timedelta(
                minutes=duration + buffer
            )
        ):
            raise ReservationRepositoryError(
                "Invalid reservation occupancy data."
            )

    return rows


def find_reservation_by_idempotency_key(
    *, idempotency_key, session
):
    """Read one existing reservation using a unique request key.

    The caller must compare customer ID, venue ID, requested
    timestamp, party size, notes, duration and buffer before
    considering this a safe replay.

    This lookup alone does not authorize or create bookings.
    """
    import re

    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise ReservationRepositoryError(
            "An active reservation transaction is required."
        )

    if (
        not isinstance(idempotency_key, str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}",
            idempotency_key,
        ) is None
    ):
        raise ReservationRepositoryError(
            "Invalid reservation idempotency key."
        )

    record = mongo.db.reservations.find_one(
        {"idempotency_key": idempotency_key},
        session=session,
    )

    if record is None:
        return None

    if (
        not isinstance(record, dict)
        or not isinstance(record.get("_id"), ObjectId)
        or not isinstance(record.get("venue_id"), ObjectId)
        or not isinstance(record.get("customer_id"), ObjectId)
        or record.get("idempotency_key") != idempotency_key
        or record.get("status") not in {
            "pending", "confirmed", "rejected",
            "cancelled", "completed", "no_show",
        }
    ):
        raise ReservationRepositoryError(
            "Invalid existing idempotency record."
        )

    start = _utc_time(
        record.get("start_at"),
        label="existing reservation start_at",
    )
    end = _utc_time(
        record.get("end_at"),
        label="existing reservation end_at",
    )

    party = record.get("party_size")
    duration = record.get("duration_minutes")
    buffer = record.get("buffer_minutes")

    if (
        start >= end
        or isinstance(party, bool)
        or not isinstance(party, int)
        or not 1 <= party <= 100
        or isinstance(duration, bool)
        or not isinstance(duration, int)
        or not 15 <= duration <= 720
        or isinstance(buffer, bool)
        or not isinstance(buffer, int)
        or not 0 <= buffer <= 180
        or end != start + timedelta(
            minutes=duration + buffer
        )
    ):
        raise ReservationRepositoryError(
            "Invalid existing reservation occupancy."
        )

    # Use verified UTC values for exact replay comparisons.
    normalized = dict(record)
    normalized["start_at"] = start
    normalized["end_at"] = end
    return normalized
