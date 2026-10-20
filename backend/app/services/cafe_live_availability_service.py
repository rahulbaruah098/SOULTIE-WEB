"""Server-owned, read-only cafe booking availability.

Availability is advisory. A successful booking must independently
recheck the live policy and reservations inside the booking fence.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId

from app.extensions import mongo
from app.schemas.cafe_venue_operations import (
    validate_cafe_booking_settings,
    validate_cafe_weekly_hours,
)
from app.schemas.cafe_booking_configuration import (
    validate_cafe_booking_configuration,
)
from app.schemas.reservation import ACTIVE_RESERVATION_STATUSES
from app.services.cafe_customer_availability_service import (
    CafeAvailabilityError,
    calculate_cafe_availability,
)


class CafeLiveAvailabilityError(ValueError):
    """Required authoritative availability data is unavailable."""


def _mongo_utc(value, name):
    if not isinstance(value, datetime):
        raise CafeLiveAvailabilityError(
            "Invalid reservation " + name + "."
        )
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    if value.utcoffset() is None:
        raise CafeLiveAvailabilityError(
            "Invalid reservation " + name + "."
        )
    return value.astimezone(timezone.utc)


def read_live_cafe_availability(
    *,
    venue_id,
    day,
    party_size,
    now=None,
):
    """Compute advisory slots from real booking configuration.

    Only approved active venues qualify. A bounded, complete snapshot
    of active reservations is required; no incomplete snapshot is used.
    """
    if (
        not isinstance(venue_id, str)
        or len(venue_id) != 24
        or not ObjectId.is_valid(venue_id)
    ):
        raise CafeLiveAvailabilityError("Invalid venue.")

    if type(day) is not date:
        raise CafeLiveAvailabilityError("Invalid requested date.")

    if (
        type(party_size) is not int
        or not 1 <= party_size <= 100
    ):
        raise CafeLiveAvailabilityError("Invalid party size.")

    if now is None:
        current = datetime.now(timezone.utc)
    elif (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeLiveAvailabilityError(
            "Timezone-aware current time required."
        )
    else:
        current = now.astimezone(timezone.utc)

    venue = mongo.db.venues.find_one({
        "_id": ObjectId(venue_id),
        "status": "approved",
        "active": True,
    })

    if (
        not isinstance(venue, dict)
        or venue.get("_id") != ObjectId(venue_id)
        or venue.get("status") != "approved"
        or venue.get("active") is not True
    ):
        raise CafeLiveAvailabilityError(
            "Venue unavailable for bookings."
        )

    try:
        configuration = validate_cafe_booking_configuration({
            "seat_capacity": venue.get("seat_capacity"),
            "timezone": venue.get("timezone"),
        })
        weekly = validate_cafe_weekly_hours(
            venue.get("weekly_hours")
        )
        settings = validate_cafe_booking_settings(
            venue.get("booking_settings")
        )
        zone = ZoneInfo(configuration["timezone"])
    except (ValueError, TypeError, KeyError) as exc:
        raise CafeLiveAvailabilityError(
            "Venue booking configuration incomplete."
        ) from exc

    local_today = current.astimezone(zone).date()
    days_ahead = (day - local_today).days

    if (
        days_ahead < 0
        or days_ahead > settings["advance_booking_days"]
    ):
        raise CafeLiveAvailabilityError(
            "Date outside booking window."
        )

    if party_size > settings["max_party_size"]:
        return {
            "venue_id": venue_id,
            "date": day.isoformat(),
            "timezone": configuration["timezone"],
            "party_size": party_size,
            "slots": [],
            "advisory": True,
        }

    # Read the complete active set for this venue. A cap prevents
    # accidentally treating a truncated reservation list as complete.
    cursor = mongo.db.reservations.find(
        {
            "venue_id": ObjectId(venue_id),
            "status": {
                "$in": sorted(ACTIVE_RESERVATION_STATUSES)
            },
        },
        {
            "start_at": 1,
            "end_at": 1,
            "party_size": 1,
        },
    ).limit(5001)

    rows = list(cursor)

    if len(rows) > 5000:
        raise CafeLiveAvailabilityError(
            "Reservation snapshot too large."
        )

    reservations = []

    for row in rows:
        if not isinstance(row, dict):
            raise CafeLiveAvailabilityError(
                "Malformed reservation snapshot."
            )

        start = _mongo_utc(
            row.get("start_at"), "start"
        )
        end = _mongo_utc(
            row.get("end_at"), "end"
        )

        guests = row.get("party_size")
        if (
            end <= start
            or type(guests) is not int
            or not 1 <= guests <= 100
        ):
            raise CafeLiveAvailabilityError(
                "Malformed active reservation."
            )

        reservations.append({
            "start_at": start,
            "end_at": end,
            "party_size": guests,
        })

    try:
        slots = calculate_cafe_availability(
            day=day,
            timezone_name=configuration["timezone"],
            weekly_hours=weekly,
            slot_minutes=settings["slot_minutes"],
            buffer_minutes=settings["buffer_minutes"],
            seat_capacity=configuration["seat_capacity"],
            party_size=party_size,
            active_reservations=reservations,
            now=current,
        )
    except CafeAvailabilityError as exc:
        raise CafeLiveAvailabilityError(
            "Availability cannot be evaluated."
        ) from exc

    return {
        "venue_id": venue_id,
        "date": day.isoformat(),
        "timezone": configuration["timezone"],
        "party_size": party_size,
        "slots": slots,
        "advisory": True,
    }
