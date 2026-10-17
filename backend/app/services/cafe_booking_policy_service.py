"""Database-backed booking policy for Xuoroni cafe venues.

This is a fail-closed policy loader to be invoked ONLY by a
trusted booking service with an active MongoDB transaction.

The venue must explicitly configure:
- approved and active status;
- seat_capacity;
- an IANA timezone;
- complete validated weekly_hours;
- complete validated booking_settings.

Absent settings NEVER produce default capacity or opening hours.

A live owner-facing configuration endpoint and reservation-sensitive
settings update transaction are still required before reservations
can be enabled.

Customer authentication is NOT performed in this module.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bson import ObjectId

from app.extensions import mongo
from app.schemas.cafe_venue_operations import (
    validate_cafe_booking_settings,
    validate_cafe_weekly_hours,
)


class CafeBookingPolicyError(ValueError):
    """Venue booking configuration or requested time is invalid."""


def _integer(value, label, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise CafeBookingPolicyError(
            f"Invalid {label}."
        )
    return value


def _aware_utc(value, label):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeBookingPolicyError(
            f"Invalid {label}."
        )
    return value.astimezone(timezone.utc)


def _minute_of_day(hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return hour * 60 + minute


def load_cafe_booking_policy(
    venue_id,
    cleaned_request,
    session,
    *,
    now=None,
):
    """Load and verify venue policy inside a booking transaction.

    The caller MUST acquire the same venue write fence first.
    This read uses the supplied transaction session and never
    accepts capacity or operating hours from the booking client.
    """
    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise CafeBookingPolicyError(
            "An active booking transaction is required."
        )

    if (
        not isinstance(venue_id, str)
        or not ObjectId.is_valid(venue_id)
    ):
        raise CafeBookingPolicyError(
            "Invalid booking venue."
        )

    if (
        not isinstance(cleaned_request, dict)
        or cleaned_request.get("venue_id") != venue_id
    ):
        raise CafeBookingPolicyError(
            "Booking request does not match venue."
        )

    start_at = _aware_utc(
        cleaned_request.get("start_at"),
        "booking start",
    )

    party = _integer(
        cleaned_request.get("party_size"),
        "party size",
        1,
        100,
    )

    current = (
        datetime.now(timezone.utc)
        if now is None
        else _aware_utc(now, "current time")
    )

    venue = mongo.db.venues.find_one(
        {
            "_id": ObjectId(venue_id),
            "status": "approved",
            "active": True,
        },
        session=session,
    )

    if (
        not isinstance(venue, dict)
        or venue.get("_id") != ObjectId(venue_id)
        or venue.get("status") != "approved"
        or venue.get("active") is not True
    ):
        raise CafeBookingPolicyError(
            "Venue is not available for bookings."
        )

    capacity = _integer(
        venue.get("seat_capacity"),
        "venue seat capacity",
        1,
        10000,
    )

    if party > capacity:
        raise CafeBookingPolicyError(
            "Party size exceeds venue capacity."
        )

    zone_name = venue.get("timezone")

    if (
        not isinstance(zone_name, str)
        or not zone_name
        or len(zone_name) > 100
        or zone_name.strip() != zone_name
    ):
        raise CafeBookingPolicyError(
            "Venue timezone is not configured."
        )

    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise CafeBookingPolicyError(
            "Venue timezone is invalid or unavailable."
        ) from exc

    try:
        hours = validate_cafe_weekly_hours(
            venue.get("weekly_hours")
        )
        settings = validate_cafe_booking_settings(
            venue.get("booking_settings")
        )
    except ValueError as exc:
        raise CafeBookingPolicyError(
            "Venue booking configuration is incomplete."
        ) from exc

    if party > settings["max_party_size"]:
        raise CafeBookingPolicyError(
            "Party size exceeds booking limit."
        )

    if start_at <= current:
        raise CafeBookingPolicyError(
            "Bookings must be in the future."
        )

    local_start = start_at.astimezone(zone)
    local_now = current.astimezone(zone)

    if (
        local_start.date() - local_now.date()
    ).days > settings["advance_booking_days"]:
        raise CafeBookingPolicyError(
            "Booking exceeds advance booking window."
        )

    duration = settings["slot_minutes"]
    buffer = settings["buffer_minutes"]

    service_end_utc = start_at + timedelta(
        minutes=duration
    )
    service_end_local = service_end_utc.astimezone(zone)

    if service_end_local.date() != local_start.date():
        raise CafeBookingPolicyError(
            "Booking must finish on its starting local day."
        )

    start_minute = (
        local_start.hour * 60 + local_start.minute
    )
    end_minute = (
        service_end_local.hour * 60
        + service_end_local.minute
    )

    if (
        local_start.second != 0
        or local_start.microsecond != 0
        or start_minute % 15 != 0
    ):
        raise CafeBookingPolicyError(
            "Booking start must align to a 15-minute boundary."
        )

    weekday = local_start.strftime("%A").lower()
    periods = hours[weekday]
    allowed = False

    for period in periods:
        opening = _minute_of_day(period["open"])
        closing = _minute_of_day(period["close"])

        if (
            opening <= start_minute
            and end_minute <= closing
            and (start_minute - opening) % duration == 0
        ):
            allowed = True
            break

    if not allowed:
        raise CafeBookingPolicyError(
            "Requested booking is outside venue operating hours."
        )

    # No reliable venue-level concurrency limit exists yet.
    # The separately fenced seat-capacity check remains mandatory.
    return {
        "venue_id": venue_id,
        "booking_allowed": True,
        "seat_capacity": capacity,
        "duration_minutes": duration,
        "buffer_minutes": buffer,
        "max_concurrent_bookings": None,
    }
