"""Explicit venue capacity and timezone validation.

This schema is pure. It never modifies the venue or authorizes
an owner. Configuration persistence and reservation-sensitive
conflict protection must be supplied by the trusted service layer.

Do not infer timezone from address, city, state or GPS coordinates.
A missing seat capacity or timezone is not a valid booking setup.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BOOKING_CONFIGURATION_FIELDS = frozenset({
    "seat_capacity",
    "timezone",
})


class CafeBookingConfigurationError(ValueError):
    """Invalid cafe booking configuration."""


def validate_cafe_booking_configuration(data):
    """Validate a complete pair of capacity and timezone values."""
    if not isinstance(data, dict):
        raise CafeBookingConfigurationError(
            "Booking configuration must be an object."
        )

    if set(data) != BOOKING_CONFIGURATION_FIELDS:
        raise CafeBookingConfigurationError(
            "Both seat_capacity and timezone are required; "
            "additional fields are not permitted."
        )

    capacity = data["seat_capacity"]
    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or not 1 <= capacity <= 10000
    ):
        raise CafeBookingConfigurationError(
            "seat_capacity must be between 1 and 10000."
        )

    timezone_name = data["timezone"]

    if (
        not isinstance(timezone_name, str)
        or not 1 <= len(timezone_name) <= 100
        or timezone_name != timezone_name.strip()
        or any(
            ord(char) < 33 or ord(char) > 126
            for char in timezone_name
        )
    ):
        raise CafeBookingConfigurationError(
            "Invalid timezone identifier."
        )

    try:
        ZoneInfo(timezone_name)
    except (
        ZoneInfoNotFoundError,
        ValueError,
        TypeError,
    ) as exc:
        raise CafeBookingConfigurationError(
            "Timezone must be a supported IANA timezone."
        ) from exc

    return {
        "seat_capacity": capacity,
        "timezone": timezone_name,
    }
