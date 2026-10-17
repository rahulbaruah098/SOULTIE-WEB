"""Transaction-required venue write fence for cafe reservations.

Every path that creates, cancels, confirms, reschedules, or changes
booking-sensitive settings must serialize against this SAME venue
document before reading the complete reservation snapshot.

The caller must invoke this inside a MongoDB transaction and pass
the SAME transaction session to all subsequent reads and writes.

A successful fence alone neither reserves capacity nor authorizes
a customer. No standalone booking write is exposed here.

Concurrent transactions writing the same venue document conflict;
the transaction coordinator must retry the COMPLETE transaction
from its initial authorization and capacity read, never only the
final reservation insert.
"""

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo


class CafeReservationFenceError(ValueError):
    """Invalid, unavailable or inconsistent reservation fence."""


def acquire_venue_reservation_fence(*, venue_id, session):
    """Write the venue fence using an existing MongoDB transaction.

    The repository cannot independently verify that the provided
    session has an active transaction. That responsibility belongs
    to the transaction coordinator and its integration tests.
    """
    if (
        not isinstance(venue_id, str)
        or not ObjectId.is_valid(venue_id)
    ):
        raise CafeReservationFenceError(
            "Invalid reservation venue ID."
        )

    if session is None:
        raise CafeReservationFenceError(
            "A MongoDB transaction session is required."
        )

    venue_oid = ObjectId(venue_id)

    result = mongo.db.venues.find_one_and_update(
        {
            "_id": venue_oid,
            "status": "approved",
            "active": True,
        },
        {
            "$inc": {
                "reservation_fence_version": 1,
            },
        },
        return_document=ReturnDocument.AFTER,
        session=session,
    )

    if result is None:
        raise CafeReservationFenceError(
            "Venue is unavailable for reservations."
        )

    if (
        not isinstance(result, dict)
        or result.get("_id") != venue_oid
        or result.get("status") != "approved"
        or result.get("active") is not True
        or isinstance(result.get("reservation_fence_version"), bool)
        or not isinstance(
            result.get("reservation_fence_version"), int
        )
        or result["reservation_fence_version"] < 1
    ):
        raise CafeReservationFenceError(
            "Invalid venue reservation fence acknowledgement."
        )

    return {
        "venue_id": venue_id,
        "fence_version": result["reservation_fence_version"],
    }
