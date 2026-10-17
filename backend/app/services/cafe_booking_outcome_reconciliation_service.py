"""Read-only post-transaction reconciliation for cafe booking outcomes.

Use only after the original MongoDB transaction has ended and its
outcome requires authoritative inspection. This service does NOT
create, retry, cancel, or modify any reservation.

Absence of a reservation is NOT proof that an uncertain transaction
failed. It must remain unresolved and must not trigger another write.

The calling layer must already have authenticated the dating user.
The persisted reservation must still match that user's original
request exactly before any existing booking details are returned.
"""

from bson import ObjectId
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.schemas.reservation import validate_reservation_request
from app.services.cafe_reservation_idempotency_service import (
    CafeReservationIdempotencyConflict,
    CafeReservationIdempotencyError,
    resolve_existing_reservation_replay,
)


class CafeBookingOutcomeUnavailable(RuntimeError):
    """Booking outcome cannot be authoritatively verified."""


class CafeBookingOutcomeConflict(ValueError):
    """Idempotency key belongs to different booking intent."""


def reconcile_existing_cafe_booking(*, customer_id, request_data):
    """Inspect a committed reservation without another booking write.

    The original reservation's stored duration and buffer are used
    for replay verification, not today's possibly changed venue rules.
    """
    if (
        not isinstance(customer_id, str)
        or not ObjectId.is_valid(customer_id)
    ):
        raise CafeBookingOutcomeUnavailable(
            "Customer identity cannot be verified."
        )

    customer_id = str(ObjectId(customer_id))
    cleaned = validate_reservation_request(request_data)

    try:
        existing = mongo.db.reservations.find_one({
            "idempotency_key": cleaned["idempotency_key"],
        })
    except PyMongoError as exc:
        raise CafeBookingOutcomeUnavailable(
            "Authoritative reservation lookup unavailable."
        ) from exc

    if existing is None:
        raise CafeBookingOutcomeUnavailable(
            "No authoritative booking outcome is available."
        )

    if not isinstance(existing, dict):
        raise CafeBookingOutcomeUnavailable(
            "Invalid authoritative reservation record."
        )

    duration = existing.get("duration_minutes")
    buffer = existing.get("buffer_minutes")

    try:
        result = resolve_existing_reservation_replay(
            existing=existing,
            customer_id=customer_id,
            request_data=request_data,
            duration_minutes=duration,
            buffer_minutes=buffer,
        )
    except CafeReservationIdempotencyConflict as exc:
        raise CafeBookingOutcomeConflict(
            "Idempotency key conflicts with the original request."
        ) from exc
    except (CafeReservationIdempotencyError, ValueError, TypeError) as exc:
        raise CafeBookingOutcomeUnavailable(
            "Existing booking evidence is not trustworthy."
        ) from exc

    if (
        not isinstance(result, dict)
        or result.get("replayed") is not True
        or result.get("reservation_id") != str(existing.get("_id"))
        or result.get("status") != existing.get("status")
        or result.get("venue_id") != cleaned["venue_id"]
    ):
        raise CafeBookingOutcomeUnavailable(
            "Existing booking could not be verified."
        )

    return result
