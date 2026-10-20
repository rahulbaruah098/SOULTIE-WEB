"""Pure availability calculation for Xuoroni cafe reservations.

This module produces advisory availability only. Booking creation must
repeat all checks against live MongoDB data inside the shared-fence
transaction. It does not authorize users or access the database.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.cafe_venue_operations import (
    validate_cafe_weekly_hours,
)


class CafeAvailabilityError(ValueError):
    """Availability input is invalid or cannot be safely evaluated."""


def _utc(value, label):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeAvailabilityError(
            f"{label} must be a timezone-aware datetime."
        )
    return value.astimezone(timezone.utc)


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise CafeAvailabilityError(f"Invalid {label}.")
    return value


def _minute(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def calculate_cafe_availability(
    *,
    day,
    timezone_name,
    weekly_hours,
    slot_minutes,
    buffer_minutes,
    seat_capacity,
    party_size,
    active_reservations,
    now,
    max_concurrent_bookings=None,
):
    """Return candidate slots on a local calendar date.

    Existing reservations must be obtained from an authoritative,
    complete server-side snapshot. A partial snapshot is not safe.
    """
    if type(day) is not date:
        raise CafeAvailabilityError("A local date is required.")

    if not isinstance(timezone_name, str):
        raise CafeAvailabilityError("Invalid venue timezone.")

    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise CafeAvailabilityError(
            "Unavailable venue timezone."
        ) from exc

    try:
        hours = validate_cafe_weekly_hours(weekly_hours)
    except ValueError as exc:
        raise CafeAvailabilityError(
            "Invalid venue opening hours."
        ) from exc

    duration = _integer(slot_minutes, "slot duration", 15, 720)
    buffer = _integer(buffer_minutes, "booking buffer", 0, 180)
    capacity = _integer(seat_capacity, "seat capacity", 1, 10000)
    party = _integer(party_size, "party size", 1, 100)

    if max_concurrent_bookings is not None:
        maximum = _integer(
            max_concurrent_bookings,
            "concurrent booking limit",
            1,
            10000,
        )
    else:
        maximum = None

    current = _utc(now, "Current time")

    if not isinstance(active_reservations, list):
        raise CafeAvailabilityError(
            "Complete reservation snapshot required."
        )

    if len(active_reservations) > 5000:
        raise CafeAvailabilityError(
            "Reservation snapshot exceeds safe evaluation limit."
        )

    reservations = []

    for row in active_reservations:
        if not isinstance(row, dict):
            raise CafeAvailabilityError(
                "Invalid reservation snapshot row."
            )

        start = _utc(row.get("start_at"), "Reservation start")
        end = _utc(row.get("end_at"), "Reservation end")
        guests = _integer(
            row.get("party_size"), "reservation party size", 1, 100
        )

        if end <= start:
            raise CafeAvailabilityError(
                "Invalid reservation interval."
            )

        reservations.append((start, end, guests))

    if party > capacity:
        return []

    weekday = day.strftime("%A").lower()
    slots = []

    for period in hours[weekday]:
        opening = _minute(period["open"])
        closing = _minute(period["close"])

        start_minute = opening

        while start_minute + duration <= closing:
            # Match the existing booking policy's 15-minute boundary.
            if start_minute % 15:
                start_minute += duration
                continue

            local_start = datetime.combine(
                day, time.min, tzinfo=zone
            ) + timedelta(minutes=start_minute)

            # Reject imaginary DST local times. The date and clock
            # reading must round-trip through UTC unchanged.
            start_utc = local_start.astimezone(timezone.utc)
            round_trip = start_utc.astimezone(zone)

            if (
                round_trip.date() != day
                or round_trip.hour * 60 + round_trip.minute
                != start_minute
            ):
                start_minute += duration
                continue

            service_end = start_utc + timedelta(minutes=duration)
            local_end = service_end.astimezone(zone)

            if (
                local_end.date() != day
                or (
                    local_end.hour * 60 + local_end.minute
                ) > closing
            ):
                start_minute += duration
                continue

            if start_utc <= current:
                start_minute += duration
                continue

            occupied_end = service_end + timedelta(minutes=buffer)

            overlapping = [
                (existing_start, existing_end, guests)
                for existing_start, existing_end, guests
                in reservations
                if (
                    existing_start < occupied_end
                    and existing_end > start_utc
                )
            ]

            # Sweep all interval boundaries. Checking only total
            # overlapping guests would overcount non-concurrent
            # reservations and incorrectly suppress valid slots.
            boundaries = {start_utc, occupied_end}

            for existing_start, existing_end, _ in overlapping:
                boundaries.add(max(start_utc, existing_start))
                boundaries.add(min(occupied_end, existing_end))

            available = True

            for instant in sorted(boundaries):
                if instant >= occupied_end:
                    continue

                concurrent = sum(
                    1 for beginning, ending, _
                    in overlapping
                    if beginning <= instant < ending
                )
                occupied_seats = sum(
                    guests for beginning, ending, guests
                    in overlapping
                    if beginning <= instant < ending
                )

                if (
                    occupied_seats + party > capacity
                    or (
                        maximum is not None
                        and concurrent + 1 > maximum
                    )
                ):
                    available = False
                    break

            if available:
                slots.append({
                    "start_at": start_utc.isoformat(),
                    "service_end_at": service_end.isoformat(),
                    "occupied_until": occupied_end.isoformat(),
                    "available": True,
                })

            start_minute += duration

            if len(slots) > 500:
                raise CafeAvailabilityError(
                    "Availability output limit exceeded."
                )

    return slots
