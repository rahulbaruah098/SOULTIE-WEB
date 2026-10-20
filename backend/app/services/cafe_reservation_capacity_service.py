"""Pure interval-aware cafe reservation capacity evaluation.

This module does not access MongoDB, authorize users, assign tables,
hold capacity, or create bookings. An available evaluation is NOT a
reservation guarantee. Callers must provide a complete, trustworthy
snapshot and perform an atomic capacity reservation before writing.

Capacity is a venue-level seat budget for this initial evaluator.
Table-specific constraints require a separate table-allocation layer.
"""

from datetime import datetime, timedelta, timezone

from app.schemas.reservation import ACTIVE_RESERVATION_STATUSES


class CafeCapacityError(ValueError):
    """Capacity cannot be safely evaluated."""


MAX_CAPACITY_ROWS = 500


def _positive_integer(value, label, minimum=1, maximum=10000):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise CafeCapacityError(f"Invalid {label}.")
    return value


def _utc(value, label):
    # BSON UTC capacity normalization: PyMongo naive timestamps.
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeCapacityError(f"Invalid {label}.")
    return value.astimezone(timezone.utc)


def _minutes(value, label, minimum, maximum):
    return _positive_integer(value, label, minimum, maximum)


def evaluate_reservation_capacity(
    *,
    start_at,
    party_size,
    duration_minutes,
    buffer_minutes,
    seat_capacity,
    reservations,
    max_concurrent_bookings=None,
):
    """Evaluate interval overlap against explicit venue capacity.

    Every existing active reservation must carry:
      start_at, party_size, duration_minutes, status.

    Intervals use [start, end), where end includes the buffer.
    A reservation ending exactly as the requested interval begins
    does not overlap. Reject incomplete or contradictory snapshots.

    For exact seat enforcement, use a sweep-line calculation rather
    than summing every overlapping party, since different bookings
    may overlap different subintervals of the request.
    """
    requested_start = _utc(start_at, "start_at")
    requested_party = _positive_integer(
        party_size, "party_size", 1, 100
    )
    duration = _minutes(
        duration_minutes, "duration_minutes", 15, 720
    )
    buffer = _positive_integer(
        buffer_minutes, "buffer_minutes", 0, 180
    )
    seats = _positive_integer(
        seat_capacity, "seat_capacity", 1, 10000
    )

    if max_concurrent_bookings is not None:
        maximum_bookings = _positive_integer(
            max_concurrent_bookings,
            "max_concurrent_bookings",
            1,
            10000,
        )
    else:
        maximum_bookings = None

    if not isinstance(reservations, (list, tuple)):
        raise CafeCapacityError(
            "A complete reservation snapshot is required."
        )
    if len(reservations) > MAX_CAPACITY_ROWS:
        raise CafeCapacityError(
            "Reservation snapshot exceeds safe limits."
        )

    requested_end = requested_start + timedelta(
        minutes=duration + buffer
    )

    # Events contain (timestamp, change in occupied seats,
    # change in concurrent booking count). Ending events precede
    # starting events at equal timestamps because half-open
    # intervals do not overlap at their common boundary.
    events = [
        (requested_start, requested_party, 1),
        (requested_end, -requested_party, -1),
    ]

    overlapping = 0

    for reservation in reservations:
        if not isinstance(reservation, dict):
            raise CafeCapacityError(
                "Invalid reservation record."
            )

        status = reservation.get("status")
        if status not in ACTIVE_RESERVATION_STATUSES:
            raise CafeCapacityError(
                "Reservation snapshot contains inactive status."
            )

        existing_start = _utc(
            reservation.get("start_at"),
            "existing start_at",
        )
        existing_party = _positive_integer(
            reservation.get("party_size"),
            "existing party_size",
            1,
            100,
        )
        existing_duration = _minutes(
            reservation.get("duration_minutes"),
            "existing duration_minutes",
            15,
            720,
        )
        existing_buffer = _positive_integer(
            reservation.get("buffer_minutes", 0),
            "existing buffer_minutes",
            0,
            180,
        )
        existing_end = existing_start + timedelta(
            minutes=existing_duration + existing_buffer
        )

        if (
            existing_start < requested_end
            and existing_end > requested_start
        ):
            overlapping += 1
            # Clip events to the requested booking interval.
            events.append((
                max(existing_start, requested_start),
                existing_party,
                1,
            ))
            events.append((
                min(existing_end, requested_end),
                -existing_party,
                -1,
            ))

    events.sort(
        key=lambda event: (
            event[0],
            0 if event[1] < 0 else 1,
        )
    )

    current_seats = 0
    current_bookings = 0
    peak_seats = 0
    peak_bookings = 0

    for _, seat_delta, booking_delta in events:
        current_seats += seat_delta
        current_bookings += booking_delta

        if current_seats < 0 or current_bookings < 0:
            raise CafeCapacityError(
                "Reservation event accounting is invalid."
            )

        peak_seats = max(peak_seats, current_seats)
        peak_bookings = max(
            peak_bookings,
            current_bookings,
        )

    allowed = (
        peak_seats <= seats
        and (
            maximum_bookings is None
            or peak_bookings <= maximum_bookings
        )
    )

    return {
        "available": allowed,
        "requested_party_size": requested_party,
        "seat_capacity": seats,
        "peak_occupied_seats": peak_seats,
        "peak_concurrent_bookings": peak_bookings,
        "overlapping_reservations": overlapping,
        "start_at": requested_start,
        "end_at": requested_end,
    }
