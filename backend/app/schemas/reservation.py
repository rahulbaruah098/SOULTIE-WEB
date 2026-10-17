"""Xuoroni cafe reservation validation and lifecycle rules.

This module does not read/write MongoDB, authenticate users,
allocate tables, promise availability, or send notifications.

All actual booking transitions require authorization and
atomic concurrency protection in the service/repository layer.
"""

from datetime import datetime, timezone
import re

from bson import ObjectId


class ReservationValidationError(ValueError):
    """Invalid cafe reservation request."""


class ReservationTransitionError(ValueError):
    """Unsupported cafe reservation state transition."""


RESERVATION_PENDING = "pending"
RESERVATION_CONFIRMED = "confirmed"
RESERVATION_REJECTED = "rejected"
RESERVATION_CANCELLED = "cancelled"
RESERVATION_COMPLETED = "completed"
RESERVATION_NO_SHOW = "no_show"

RESERVATION_STATUSES = frozenset({
    RESERVATION_PENDING,
    RESERVATION_CONFIRMED,
    RESERVATION_REJECTED,
    RESERVATION_CANCELLED,
    RESERVATION_COMPLETED,
    RESERVATION_NO_SHOW,
})

ACTIVE_RESERVATION_STATUSES = frozenset({
    RESERVATION_PENDING,
    RESERVATION_CONFIRMED,
})

RESERVATION_TRANSITIONS = {
    RESERVATION_PENDING: frozenset({
        RESERVATION_CONFIRMED,
        RESERVATION_REJECTED,
        RESERVATION_CANCELLED,
    }),
    RESERVATION_CONFIRMED: frozenset({
        RESERVATION_CANCELLED,
        RESERVATION_COMPLETED,
        RESERVATION_NO_SHOW,
    }),
    RESERVATION_REJECTED: frozenset(),
    RESERVATION_CANCELLED: frozenset(),
    RESERVATION_COMPLETED: frozenset(),
    RESERVATION_NO_SHOW: frozenset(),
}

RESERVATION_REQUEST_FIELDS = frozenset({
    "venue_id",
    "start_at",
    "party_size",
    "idempotency_key",
    "notes",
})

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


def _venue_id(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise ReservationValidationError(
            "A valid venue ID is required."
        )
    return str(ObjectId(value))


def _start_at(value):
    if not isinstance(value, str):
        raise ReservationValidationError(
            "Reservation time must be an ISO 8601 timestamp."
        )

    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00"
            if value.endswith("Z") else value
        )
    except (ValueError, OverflowError):
        raise ReservationValidationError(
            "Invalid reservation timestamp."
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReservationValidationError(
            "Reservation timestamp must include a timezone."
        )

    result = parsed.astimezone(timezone.utc)

    if result.second != 0 or result.microsecond != 0:
        raise ReservationValidationError(
            "Reservation start must be minute-aligned."
        )

    return result


def _party_size(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 100
    ):
        raise ReservationValidationError(
            "Party size must be between 1 and 100."
        )
    return value


def _idempotency_key(value):
    if not isinstance(value, str):
        raise ReservationValidationError(
            "A valid idempotency key is required."
        )
    if not _KEY_PATTERN.fullmatch(value):
        raise ReservationValidationError(
            "Invalid idempotency key."
        )
    return value


def _notes(value):
    if value is None:
        return ""

    if not isinstance(value, str):
        raise ReservationValidationError(
            "Reservation notes must be text."
        )

    cleaned = value.strip()

    if len(cleaned) > 500:
        raise ReservationValidationError(
            "Reservation notes exceed the limit."
        )

    return cleaned


def validate_reservation_request(data):
    """Validate reservation intent without promising availability.

    A booking must be created only after live venue eligibility,
    scheduling, capacity, identity and duplicate-request checks.
    """
    if not isinstance(data, dict):
        raise ReservationValidationError(
            "Reservation request must be a JSON object."
        )

    if set(data) - RESERVATION_REQUEST_FIELDS:
        raise ReservationValidationError(
            "Unsupported reservation request fields."
        )

    required = {
        "venue_id",
        "start_at",
        "party_size",
        "idempotency_key",
    }

    if not required.issubset(data):
        raise ReservationValidationError(
            "Required reservation fields are missing."
        )

    return {
        "venue_id": _venue_id(data["venue_id"]),
        "start_at": _start_at(data["start_at"]),
        "party_size": _party_size(data["party_size"]),
        "idempotency_key": _idempotency_key(
            data["idempotency_key"]
        ),
        "notes": _notes(data.get("notes")),
    }


def validate_reservation_transition(current_status, decision):
    """Return the requested target status when legally reachable."""
    if (
        not isinstance(current_status, str)
        or not isinstance(decision, str)
        or current_status not in RESERVATION_STATUSES
        or decision not in RESERVATION_STATUSES
        or decision not in RESERVATION_TRANSITIONS[current_status]
    ):
        raise ReservationTransitionError(
            "Reservation status transition is not permitted."
        )

    return decision
