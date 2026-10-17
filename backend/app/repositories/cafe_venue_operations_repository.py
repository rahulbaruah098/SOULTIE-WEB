"""Owner-scoped persistence for Xuoroni cafe operational settings.

This repository does not authenticate sessions, authorize actors,
make booking promises, or change cafe moderation decisions.
Call it only after live cafe-partner authorization.

Booking conflict evaluation must precede production use of
changes that can affect already accepted reservations.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo
from app.schemas.cafe_venue_operations import (
    validate_cafe_operations_update,
)


class CafeVenueOperationsRepositoryError(ValueError):
    """An invalid cafe venue operations persistence request."""


def _object_id(value, *, field):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeVenueOperationsRepositoryError(
            f"Invalid {field}."
        )
    return ObjectId(value)


def _now_utc():
    return datetime.now(timezone.utc)


def update_owned_cafe_operations(
    *, partner_id, venue_id, changes, now=None
):
    """Atomically update allowlisted fields for one approved venue.

    The owner is bound in the Mongo query. Neither approval state
    nor venue ownership is ever written by this operation.

    Return the updated venue document or None if it is not
    currently owned, active, and approved.
    """
    partner_oid = _object_id(partner_id, field="partner ID")
    venue_oid = _object_id(venue_id, field="venue ID")
    clean = validate_cafe_operations_update(changes)

    if now is None:
        timestamp = _now_utc()
    else:
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise CafeVenueOperationsRepositoryError(
                "Timezone-aware timestamp required."
            )
        timestamp = now.astimezone(timezone.utc)

    return mongo.db.venues.find_one_and_update(
        {
            "_id": venue_oid,
            "owner_id": str(partner_oid),
            "status": "approved",
            "active": True,
        },
        {
            "$set": {
                **clean,
                "updated_at": timestamp,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
