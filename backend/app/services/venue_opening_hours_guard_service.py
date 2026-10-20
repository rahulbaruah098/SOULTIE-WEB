"""Protect existing reservation intervals from venue schedule edits.

The repository/API adapter must supply active reservation intervals as
{"start_at": aware datetime, "end_at": aware datetime}.
This pure service does not read MongoDB or modify venue documents.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.schemas.venue_opening_hours import (
    DAYS,
    validate_venue_opening_hours,
)


class VenueHoursBookingConflict(ValueError):
    """Proposed venue hours would invalidate an existing booking."""


def _minute(text):
    if text == "24:00":
        return 1440
    hour, minute = map(int, text.split(":"))
    return hour * 60 + minute


def ensure_reservations_fit_opening_hours(
    opening_hours,
    active_reservations,
):
    """Return validated hours or reject reservations outside them.

    Existing reservations must be normalized by the caller. Times are
    aware UTC instants; the venue's IANA timezone determines weekdays.
    Overnight reservations are conservatively rejected by this contract.
    """
    clean = validate_venue_opening_hours(opening_hours)

    if not isinstance(active_reservations, (list, tuple)):
        raise VenueHoursBookingConflict(
            "Active reservation intervals are required."
        )

    zone = ZoneInfo(clean["timezone"])

    for reservation in active_reservations:
        if not isinstance(reservation, dict) or set(reservation) != {
            "start_at", "end_at",
        }:
            raise VenueHoursBookingConflict(
                "Invalid reservation interval."
            )

        start = reservation["start_at"]
        end = reservation["end_at"]

        if (
            not isinstance(start, datetime)
            or not isinstance(end, datetime)
            or start.tzinfo is None
            or end.tzinfo is None
            or start.utcoffset() is None
            or end.utcoffset() is None
        ):
            raise VenueHoursBookingConflict(
                "Timezone-aware reservation times required."
            )

        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)

        if end_utc <= start_utc:
            raise VenueHoursBookingConflict(
                "Invalid reservation duration."
            )

        local_start = start_utc.astimezone(zone)
        local_end = end_utc.astimezone(zone)

        # Fail closed for a reservation spanning different local dates,
        # including end-at-midnight cases. Overnight booking support
        # requires a separate explicit availability policy.
        if local_start.date() != local_end.date():
            raise VenueHoursBookingConflict(
                "Reservation crosses a venue-local date boundary."
            )

        weekday = DAYS[local_start.weekday()]
        periods = clean["weekly"][weekday]

        start_minute = (
            local_start.hour * 60
            + local_start.minute
            + local_start.second / 60
            + local_start.microsecond / 60000000
        )
        end_minute = (
            local_end.hour * 60
            + local_end.minute
            + local_end.second / 60
            + local_end.microsecond / 60000000
        )

        fits = any(
            _minute(period["open"]) <= start_minute
            and end_minute <= _minute(period["close"])
            for period in periods
        )

        if not fits:
            raise VenueHoursBookingConflict(
                "Opening hours conflict with an active reservation."
            )

    return clean
