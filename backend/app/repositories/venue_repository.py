"""Venue persistence for Soultie caf? partner management.

The authenticated service layer supplies trusted owner and admin IDs.
Database methods never accept client-supplied MongoDB operators.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo
from app.schemas.venue import (
    VENUE_STATUS_PENDING,
    VENUE_STATUS_APPROVED,
    VENUE_STATUS_REJECTED,
    VENUE_STATUS_SUSPENDED,
    VENUE_STATUSES,
    validate_cafe_registration,
    validate_venue_profile,
)


class VenueRepositoryError(ValueError):
    """Invalid identifiers, moderation decisions, or pagination."""


def _object_id(value):
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise VenueRepositoryError("Invalid venue identifier.")
    return ObjectId(value)


def _actor_id(value):
    if isinstance(value, ObjectId):
        return str(value)
    if not isinstance(value, str):
        raise VenueRepositoryError("Invalid actor identifier.")
    value = value.strip()
    if (
        not value
        or len(value) > 128
        or any(ord(character) < 33 for character in value)
    ):
        raise VenueRepositoryError("Invalid actor identifier.")
    return value


def _timestamp(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise VenueRepositoryError("Timezone-aware datetime required.")
    return value.astimezone(timezone.utc)


def create_pending_venue(registration, *, owner_id, now=None):
    owner = _actor_id(owner_id)
    clean = validate_cafe_registration(registration)
    timestamp = _timestamp(now)

    document = {
        **clean,
        "owner_id": owner,
        "status": VENUE_STATUS_PENDING,
        "status_version": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_reason": None,
    }

    result = mongo.db.venues.insert_one(document)
    return result.inserted_id


def get_venue_by_id(venue_id):
    """For service-layer callers with verified access."""
    return mongo.db.venues.find_one({
        "_id": _object_id(venue_id)
    })


def get_owned_venue(venue_id, *, owner_id):
    return mongo.db.venues.find_one({
        "_id": _object_id(venue_id),
        "owner_id": _actor_id(owner_id),
    })


def update_owned_venue_profile(
    venue_id, *, owner_id, changes, now=None
):
    clean = validate_venue_profile(changes)
    timestamp = _timestamp(now)

    return mongo.db.venues.find_one_and_update(
        {
            "_id": _object_id(venue_id),
            "owner_id": _actor_id(owner_id),
            "status": {
                "$in": [
                    VENUE_STATUS_PENDING,
                    VENUE_STATUS_APPROVED,
                    VENUE_STATUS_REJECTED,
                ],
            },
        },
        {
            "$set": {
                **clean,
                "status": VENUE_STATUS_PENDING,
                "updated_at": timestamp,
                "reviewed_at": None,
                "reviewed_by": None,
                "review_reason": None,
            },
            "$inc": {"status_version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )


TRANSITIONS = {
    VENUE_STATUS_PENDING: frozenset({
        VENUE_STATUS_APPROVED,
        VENUE_STATUS_REJECTED,
    }),
    VENUE_STATUS_APPROVED: frozenset({
        VENUE_STATUS_SUSPENDED,
    }),
    VENUE_STATUS_REJECTED: frozenset({
        VENUE_STATUS_APPROVED,
    }),
    VENUE_STATUS_SUSPENDED: frozenset({
        VENUE_STATUS_APPROVED,
    }),
}


def decide_venue_status(
    venue_id,
    *,
    superadmin_id,
    expected_status,
    decision,
    reason=None,
    now=None,
):
    actor = _actor_id(superadmin_id)
    venue = _object_id(venue_id)

    if (
        not isinstance(expected_status, str)
        or expected_status not in TRANSITIONS
        or not isinstance(decision, str)
        or decision not in TRANSITIONS[expected_status]
    ):
        raise VenueRepositoryError("Invalid venue status transition.")

    if reason is not None:
        if not isinstance(reason, str):
            raise VenueRepositoryError("Review reason must be text.")
        reason = reason.strip()
        if len(reason) > 1000:
            raise VenueRepositoryError("Review reason is too long.")

    if decision in {
        VENUE_STATUS_REJECTED,
        VENUE_STATUS_SUSPENDED,
    } and not reason:
        raise VenueRepositoryError(
            "Rejection or suspension requires a reason."
        )

    timestamp = _timestamp(now)

    return mongo.db.venues.find_one_and_update(
        {
            "_id": venue,
            "status": expected_status,
        },
        {
            "$set": {
                "status": decision,
                "updated_at": timestamp,
                "reviewed_at": timestamp,
                "reviewed_by": actor,
                "review_reason": reason,
            },
            "$inc": {"status_version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )


def list_venues_for_superadmin(
    *, status=None, limit=25, after_id=None
):
    if status is not None and (
        not isinstance(status, str)
        or status not in VENUE_STATUSES
    ):
        raise VenueRepositoryError("Invalid status filter.")

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise VenueRepositoryError("Invalid page size.")

    query = {}

    if status is not None:
        query["status"] = status

    if after_id is not None:
        query["_id"] = {"$gt": _object_id(after_id)}

    return list(
        mongo.db.venues.find(query)
        .sort("_id", 1)
        .limit(limit)
    )
