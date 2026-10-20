"""Atomic, booking-safe venue opening-hours updates.

The caller must provide the existing booking write-fence adapter.
This module deliberately does not invent an independent locking
scheme or enable an HTTP endpoint.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo
from app.schemas.cafe_venue_operations import (
    validate_cafe_weekly_hours,
)
from app.schemas.venue_opening_hours import (
    validate_venue_opening_hours,
)
from app.repositories.venue_hours_reservation_repository import (
    read_active_hours_reservation_intervals,
)
from app.services.venue_opening_hours_guard_service import (
    ensure_reservations_fit_opening_hours,
)
from app.services.cafe_reservation_transaction_service import (
    run_cafe_reservation_transaction,
)


class VenueHoursUpdateError(RuntimeError):
    """Opening-hours update could not be performed safely."""


def update_venue_opening_hours_atomically(
    *,
    venue_id,
    partner_id,
    proposed_hours,
    acquire_booking_fence,
):
    """Check and save hours under the existing booking write fence.

    acquire_booking_fence must be a callable accepting keyword-only
    venue_id and session. It must acquire the SAME MongoDB venue
    write fence used by the booking creation coordinator.
    It must raise on failure and return exactly True on success.

    The guard checks active reservation intervals within the same
    transaction after fence acquisition and before the update.
    """
    if (
        not isinstance(venue_id, str)
        or len(venue_id) != 24
        or not ObjectId.is_valid(venue_id)
        or not isinstance(partner_id, str)
        or len(partner_id) != 24
        or not ObjectId.is_valid(partner_id)
    ):
        raise VenueHoursUpdateError("Invalid venue ownership context.")

    if not callable(acquire_booking_fence):
        raise VenueHoursUpdateError(
            "Existing booking fence adapter is required."
        )

    clean = validate_venue_opening_hours(proposed_hours)

    # Enforce the actual weekly-hours schema consumed by booking policy.
    booking_weekly_hours = validate_cafe_weekly_hours(
        clean["weekly"]
    )
    venue_oid = ObjectId(venue_id)

    def operation(session):
        if (
            session is None
            or getattr(session, "in_transaction", False) is not True
        ):
            raise VenueHoursUpdateError(
                "Active transaction required."
            )

        ownership = mongo.db.venues.find_one(
            {
                "_id": venue_oid,
                "owner_id": partner_id,
                "status": "approved",
                "active": True,
            },
            {
                "_id": 1,
                "owner_id": 1,
                "status": 1,
                "active": 1,
                "timezone": 1,
            },
            session=session,
        )

        if (
            not isinstance(ownership, dict)
            or ownership.get("_id") != venue_oid
            or ownership.get("owner_id") != partner_id
            or ownership.get("status") != "approved"
            or ownership.get("active") is not True
        ):
            raise VenueHoursUpdateError(
                "Approved active venue ownership not found."
            )

        if ownership.get("timezone") != clean["timezone"]:
            raise VenueHoursUpdateError(
                "Opening-hours timezone must match booking timezone."
            )

        account = mongo.db.cafe_partner_accounts.find_one(
            {
                "_id": ObjectId(partner_id),
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "venue_id": venue_oid,
            },
            session=session,
        )

        if (
            not isinstance(account, dict)
            or account.get("_id") != ObjectId(partner_id)
            or account.get("account_type") != "cafe_partner"
            or account.get("status") != "approved"
            or account.get("email_verified") is not True
            or account.get("venue_id") != venue_oid
        ):
            raise VenueHoursUpdateError(
                "Verified cafe partner ownership unavailable."
            )

        # The existing booking implementation must provide this
        # shared concurrency fence. No separate lock is allowed.
        acquired = acquire_booking_fence(
            venue_id=venue_id,
            session=session,
        )

        if acquired is not True:
            raise VenueHoursUpdateError(
                "Booking write fence was not acquired."
            )

        intervals = read_active_hours_reservation_intervals(
            venue_id=venue_id,
            session=session,
        )

        verified_hours = ensure_reservations_fit_opening_hours(
            clean,
            intervals,
        )

        now = datetime.now(timezone.utc)

        updated = mongo.db.venues.find_one_and_update(
            {
                "_id": venue_oid,
                "owner_id": partner_id,
                "status": "approved",
                "active": True,
                "timezone": clean["timezone"],
            },
            {
                "$set": {
                    "weekly_hours": booking_weekly_hours,
                    "opening_hours": verified_hours,
                    "opening_hours_updated_at": now,
                    "updated_at": now,
                },
            },
            projection={
                "_id": 1,
                "status": 1,
                "owner_id": 1,
                "active": 1,
                "timezone": 1,
                "weekly_hours": 1,
                "opening_hours": 1,
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )

        if (
            not isinstance(updated, dict)
            or updated.get("_id") != venue_oid
            or updated.get("owner_id") != partner_id
            or updated.get("status") != "approved"
            or updated.get("active") is not True
            or updated.get("timezone") != clean["timezone"]
            or updated.get("weekly_hours") != booking_weekly_hours
            or updated.get("opening_hours") != verified_hours
        ):
            raise VenueHoursUpdateError(
                "Opening-hours database acknowledgement invalid."
            )

        return {
            "venue_id": venue_id,
            "status": "approved",
            "timezone": clean["timezone"],
            "weekly_hours": booking_weekly_hours,
            "opening_hours": verified_hours,
        }

    return run_cafe_reservation_transaction(
        operation=operation,
    )
