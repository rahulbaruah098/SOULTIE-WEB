"""Strict weekly opening-hours contract for Xuoroni venues.

This module validates business opening periods only.
Reservation eligibility also requires capacity and conflict checks.
"""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)

TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class VenueOpeningHoursError(ValueError):
    """Invalid weekly venue opening schedule."""


def _minute(value, *, closing=False):
    if closing and value == "24:00":
        return 1440
    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
        raise VenueOpeningHoursError("Invalid opening-hours time.")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def validate_venue_opening_hours(data):
    """Validate the complete venue-local weekly operating schedule.

    Input:
      {"timezone": "Asia/Kolkata", "weekly": {
        "monday": [{"open": "09:00", "close": "21:00"}],
        ... all other days ...
      }}

    An empty list means closed on that day.
    Overnight periods are not supported by this initial contract;
    the subsequent calendar availability service must also account
    for exceptional closures and existing reservations.
    """
    if not isinstance(data, dict) or set(data) != {
        "timezone", "weekly",
    }:
        raise VenueOpeningHoursError(
            "Timezone and complete weekly schedule are required."
        )

    timezone = data["timezone"]
    if (
        not isinstance(timezone, str)
        or not 1 <= len(timezone) <= 64
    ):
        raise VenueOpeningHoursError("Invalid venue timezone.")

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise VenueOpeningHoursError(
            "Unknown venue timezone."
        ) from None

    weekly = data["weekly"]
    if not isinstance(weekly, dict) or set(weekly) != set(DAYS):
        raise VenueOpeningHoursError(
            "All seven weekdays must be provided."
        )

    normalized = {}

    for day in DAYS:
        periods = weekly[day]
        if not isinstance(periods, list) or len(periods) > 4:
            raise VenueOpeningHoursError(
                "Invalid number of opening periods."
            )

        clean = []

        for period in periods:
            if not isinstance(period, dict) or set(period) != {
                "open", "close",
            }:
                raise VenueOpeningHoursError(
                    "Invalid opening period."
                )

            start = _minute(period["open"])
            end = _minute(period["close"], closing=True)

            if start >= end:
                raise VenueOpeningHoursError(
                    "Opening period must end after it starts."
                )

            clean.append((start, end, period))

        clean.sort(key=lambda entry: entry[0])

        for previous, current in zip(clean, clean[1:]):
            if current[0] < previous[1]:
                raise VenueOpeningHoursError(
                    "Overlapping opening periods."
                )

        normalized[day] = [
            {"open": entry[2]["open"], "close": entry[2]["close"]}
            for entry in clean
        ]

    return {
        "timezone": timezone,
        "weekly": normalized,
    }
