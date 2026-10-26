"""Validation for Soultie cafe venue operational settings.

Pure validation only. No database writes, approval decisions,
booking creation, session handling, or user authorization.

The service/API layers must independently enforce ownership,
venue approval, audit recording, and state transitions.
"""

import re


class CafeVenueOperationsError(ValueError):
    """Invalid cafe facilities, operating hours, or booking settings."""


DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

FACILITY_OPTIONS = frozenset({
    "wifi",
    "parking",
    "outdoor_seating",
    "indoor_seating",
    "wheelchair_accessible",
    "air_conditioning",
    "restroom",
    "power_outlets",
    "private_seating",
    "group_seating",
    "pet_friendly",
    "vegetarian_options",
    "vegan_options",
    "halal_options",
    "live_music",
    "quiet_area",
})

BOOKING_SETTINGS_FIELDS = frozenset({
    "max_party_size",
    "slot_minutes",
    "advance_booking_days",
    "buffer_minutes",
})

OPERATION_FIELDS = frozenset({
    "facilities",
    "weekly_hours",
    "booking_settings",
})

TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def _mapping(value, *, allowed, name):
    if not isinstance(value, dict):
        raise CafeVenueOperationsError(
            f"{name} must be an object."
        )

    invalid = set(value) - allowed
    if invalid:
        raise CafeVenueOperationsError(
            f"Unsupported {name} fields."
        )

    return value


def _integer(value, *, name, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise CafeVenueOperationsError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _minute_of_day(value):
    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
        raise CafeVenueOperationsError(
            "Time must use HH:MM (24-hour clock)."
        )
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_cafe_facilities(value):
    """Return a sorted, unique facility list from the approved catalog."""
    if not isinstance(value, list) or len(value) > len(FACILITY_OPTIONS):
        raise CafeVenueOperationsError(
            "Facilities must be a supported list."
        )

    if any(
        not isinstance(item, str)
        or item not in FACILITY_OPTIONS
        for item in value
    ):
        raise CafeVenueOperationsError(
            "Unsupported facility."
        )

    if len(set(value)) != len(value):
        raise CafeVenueOperationsError(
            "Duplicate facility."
        )

    return sorted(value)


def validate_cafe_weekly_hours(value):
    """Validate a complete seven-day weekly opening schedule.

    Each day is a list of zero to three non-overlapping intervals:
    [{"open": "09:00", "close": "13:00"}].

    Empty list means closed. Intervals must end on the same day.
    Overnight hours will require a separate, explicit contract.
    """
    _mapping(
        value,
        allowed=frozenset(DAYS),
        name="weekly_hours",
    )

    if set(value) != set(DAYS):
        raise CafeVenueOperationsError(
            "Weekly hours must include all seven days."
        )

    output = {}

    for day in DAYS:
        periods = value[day]

        if not isinstance(periods, list) or len(periods) > 3:
            raise CafeVenueOperationsError(
                "Each day must contain up to three periods."
            )

        clean_periods = []
        last_close = -1

        for period in periods:
            _mapping(
                period,
                allowed=frozenset({"open", "close"}),
                name="opening period",
            )

            if set(period) != {"open", "close"}:
                raise CafeVenueOperationsError(
                    "Opening period requires open and close."
                )

            opening = _minute_of_day(period["open"])
            closing = _minute_of_day(period["close"])

            if opening >= closing:
                raise CafeVenueOperationsError(
                    "Opening period must close later the same day."
                )

            if opening < last_close:
                raise CafeVenueOperationsError(
                    "Opening periods cannot overlap or be unsorted."
                )

            clean_periods.append({
                "open": period["open"],
                "close": period["close"],
            })
            last_close = closing

        output[day] = clean_periods

    return output


def validate_cafe_booking_settings(value):
    """Validate declared booking parameters, not real-time slots."""
    _mapping(
        value,
        allowed=BOOKING_SETTINGS_FIELDS,
        name="booking_settings",
    )

    if set(value) != BOOKING_SETTINGS_FIELDS:
        raise CafeVenueOperationsError(
            "All booking settings are required."
        )

    slot = _integer(
        value["slot_minutes"],
        name="slot_minutes",
        minimum=15,
        maximum=180,
    )

    if slot % 15 != 0:
        raise CafeVenueOperationsError(
            "slot_minutes must be a multiple of 15."
        )

    buffer = _integer(
        value["buffer_minutes"],
        name="buffer_minutes",
        minimum=0,
        maximum=120,
    )

    if buffer % 5 != 0:
        raise CafeVenueOperationsError(
            "buffer_minutes must be a multiple of 5."
        )

    return {
        "max_party_size": _integer(
            value["max_party_size"],
            name="max_party_size",
            minimum=1,
            maximum=100,
        ),
        "slot_minutes": slot,
        "advance_booking_days": _integer(
            value["advance_booking_days"],
            name="advance_booking_days",
            minimum=1,
            maximum=180,
        ),
        "buffer_minutes": buffer,
    }


def validate_cafe_operations_update(data):
    """Validate a nonempty partial operations update.

    Unknown keys, admin-only fields, and implicit approval
    changes are rejected. Validation never mutates input.
    """
    _mapping(
        data,
        allowed=OPERATION_FIELDS,
        name="venue operations",
    )

    if not data:
        raise CafeVenueOperationsError(
            "At least one operations field is required."
        )

    clean = {}

    if "facilities" in data:
        clean["facilities"] = validate_cafe_facilities(
            data["facilities"]
        )

    if "weekly_hours" in data:
        clean["weekly_hours"] = validate_cafe_weekly_hours(
            data["weekly_hours"]
        )

    if "booking_settings" in data:
        clean["booking_settings"] = (
            validate_cafe_booking_settings(
                data["booking_settings"]
            )
        )

    return clean
