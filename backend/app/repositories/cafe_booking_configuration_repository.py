"""Transaction-protected venue booking configuration persistence.

Internal repository only; not a public API.

The caller must verify the live cafe partner's session and
authorization independently before invoking this repository.

The venue document is mutated as a write fence in the SAME MongoDB
transaction used to check for active reservations and save settings.

Until detailed reservation-compatibility checks exist, any pending
or confirmed reservation blocks all booking-sensitive changes.

Do not emit notifications or other external effects in this function.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo
from app.schemas.cafe_booking_configuration import (
    validate_cafe_booking_configuration,
)
from app.schemas.cafe_venue_operations import (
    validate_cafe_booking_settings,
    validate_cafe_weekly_hours,
)
from app.schemas.reservation import ACTIVE_RESERVATION_STATUSES


class CafeBookingConfigurationDenied(ValueError):
    """Owner, venue or transaction is not eligible."""


class CafeBookingConfigurationConflict(ValueError):
    """Existing reservations prevent configuration changes."""


def _identity(value, label):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeBookingConfigurationDenied(
            f"Invalid {label}."
        )
    return str(ObjectId(value))


def save_cafe_booking_configuration(
    *,
    partner_id,
    venue_id,
    configuration,
    weekly_hours,
    booking_settings,
    session,
    now=None,
):
    """Save complete configuration for one approved, owned venue.

    Must execute inside a transaction. A same-document write fence
    serializes this change with correctly fenced reservation writes.

    Existing pending/confirmed reservations block changes, including
    older records with missing or malformed booking intervals.

    A successful result means only that this transaction's write
    was acknowledged; the caller must still commit its transaction.
    """
    owner = _identity(partner_id, "partner ID")
    venue = _identity(venue_id, "venue ID")

    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise CafeBookingConfigurationDenied(
            "Active MongoDB transaction required."
        )

    clean = validate_cafe_booking_configuration(configuration)
    hours = validate_cafe_weekly_hours(weekly_hours)
    settings = validate_cafe_booking_settings(booking_settings)

    if now is None:
        timestamp = datetime.now(timezone.utc)
    elif (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeBookingConfigurationDenied(
            "Timezone-aware timestamp required."
        )
    else:
        timestamp = now.astimezone(timezone.utc)

    venue_oid = ObjectId(venue)
    query = {
        "_id": venue_oid,
        "owner_id": owner,
        "status": "approved",
        "active": True,
    }

    fenced = mongo.db.venues.find_one_and_update(
        query,
        {"$inc": {"reservation_fence_version": 1}},
        return_document=ReturnDocument.AFTER,
        session=session,
    )

    if (
        not isinstance(fenced, dict)
        or fenced.get("_id") != venue_oid
        or fenced.get("owner_id") != owner
        or fenced.get("status") != "approved"
        or fenced.get("active") is not True
        or type(fenced.get("reservation_fence_version")) is not int
        or fenced["reservation_fence_version"] < 1
    ):
        raise CafeBookingConfigurationDenied(
            "Venue ownership or write fence could not be verified."
        )

    active = mongo.db.reservations.find_one(
        {
            "venue_id": venue_oid,
            "status": {
                "$in": sorted(ACTIVE_RESERVATION_STATUSES),
            },
        },
        session=session,
    )

    if active is not None:
        raise CafeBookingConfigurationConflict(
            "Active reservations require compatibility review."
        )

    changes = {
        **clean,
        "weekly_hours": hours,
        "booking_settings": settings,
        "updated_at": timestamp,
    }

    result = mongo.db.venues.update_one(
        {
            **query,
            "reservation_fence_version": (
                fenced["reservation_fence_version"]
            ),
        },
        {"$set": changes},
        session=session,
    )

    if (
        result is None
        or result.matched_count != 1
        or result.modified_count != 1
    ):
        raise CafeBookingConfigurationDenied(
            "Booking configuration update not acknowledged."
        )

    return {
        "venue_id": venue,
        "seat_capacity": clean["seat_capacity"],
        "timezone": clean["timezone"],
        "weekly_hours": hours,
        "booking_settings": settings,
        "configuration_saved": True,
    }
