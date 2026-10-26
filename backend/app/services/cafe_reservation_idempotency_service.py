"""Pure idempotency replay checks for Soultie cafe reservations.

Compare a validated booking request with a record fetched inside
the active booking transaction.

A matching replay returns the EXISTING reservation status, not
a new booking confirmation. No database or notification side
effects occur here.

The booking coordinator must still handle duplicate-key races,
authorization, and MongoDB transaction conflicts.
"""

from datetime import timedelta

from bson import ObjectId

from app.schemas.reservation import (
    RESERVATION_STATUSES,
    validate_reservation_request,
)


class CafeReservationIdempotencyConflict(ValueError):
    """The idempotency key belongs to a different booking request."""


class CafeReservationIdempotencyError(ValueError):
    """The idempotency replay could not be verified safely."""


def _oid(value, label):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeReservationIdempotencyError(
            f"Invalid {label}."
        )
    return ObjectId(value)


def _integer(value, label, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise CafeReservationIdempotencyError(
            f"Invalid {label}."
        )
    return value


def resolve_existing_reservation_replay(
    *,
    existing,
    customer_id,
    request_data,
    duration_minutes,
    buffer_minutes,
):
    """Return existing booking details only for an identical request.

    existing=None means the key is unused at the transaction
    snapshot and the caller may continue the guarded create flow.

    A record with the same key but different booking intent must
    raise a conflict, including after cancellation/completion.
    """
    customer_oid = _oid(customer_id, "customer ID")
    cleaned = validate_reservation_request(request_data)
    venue_oid = _oid(cleaned["venue_id"], "venue ID")

    duration = _integer(
        duration_minutes, "duration", 15, 720
    )
    buffer = _integer(
        buffer_minutes, "buffer", 0, 180
    )

    if existing is None:
        return None

    if not isinstance(existing, dict):
        raise CafeReservationIdempotencyError(
            "Invalid prior reservation."
        )

    reservation_oid = existing.get("_id")
    if not isinstance(reservation_oid, ObjectId):
        raise CafeReservationIdempotencyError(
            "Invalid prior reservation ID."
        )

    if existing.get("status") not in RESERVATION_STATUSES:
        raise CafeReservationIdempotencyError(
            "Invalid prior reservation status."
        )

    start = cleaned["start_at"]
    end = start + timedelta(minutes=duration + buffer)

    expected = {
        "customer_id": customer_oid,
        "venue_id": venue_oid,
        "idempotency_key": cleaned["idempotency_key"],
        "start_at": start,
        "end_at": end,
        "party_size": cleaned["party_size"],
        "duration_minutes": duration,
        "buffer_minutes": buffer,
        "notes": cleaned["notes"],
    }

    if any(existing.get(key) != value for key, value in expected.items()):
        raise CafeReservationIdempotencyConflict(
            "Idempotency key conflicts with another booking request."
        )

    return {
        "reservation_id": str(reservation_oid),
        "venue_id": cleaned["venue_id"],
        "status": existing["status"],
        "start_at": start,
        "end_at": end,
        "party_size": cleaned["party_size"],
        "replayed": True,
    }
