"""Adapter for Soultie's existing reservation write fence.

Booking creation, reservation lifecycle changes and booking-sensitive
configuration edits must use the same MongoDB venue document fence.

No independent lock or new fence field is introduced here.
"""

from bson import ObjectId

from app.repositories.cafe_reservation_fence_repository import (
    acquire_venue_reservation_fence,
)


class VenueHoursFenceError(RuntimeError):
    """The shared booking fence could not be verified."""


def acquire_hours_booking_fence(*, venue_id, session):
    """Acquire the existing venue fence and verify its acknowledgement.

    Return exactly True only when the existing repository confirms
    the requested venue and a valid, positive integer fence version.

    The caller must provide an active MongoDB transaction and must
    use the same transaction session for the reservation snapshot
    and opening-hours write.
    """
    if (
        not isinstance(venue_id, str)
        or len(venue_id) != 24
        or not ObjectId.is_valid(venue_id)
    ):
        raise VenueHoursFenceError("Invalid venue ID.")

    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise VenueHoursFenceError(
            "An active booking transaction is required."
        )

    acknowledgement = acquire_venue_reservation_fence(
        venue_id=venue_id,
        session=session,
    )

    if (
        not isinstance(acknowledgement, dict)
        or set(acknowledgement) != {
            "venue_id", "fence_version",
        }
        or acknowledgement["venue_id"] != venue_id
        or type(acknowledgement["fence_version"]) is not int
        or acknowledgement["fence_version"] < 1
    ):
        raise VenueHoursFenceError(
            "Invalid booking fence acknowledgement."
        )

    return True
