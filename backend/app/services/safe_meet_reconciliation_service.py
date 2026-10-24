"""Safe Meet booking-health reconciliation.

Returns an authorized, current booking-health view. This module
does not change consent, reservation status, or audit history.

A changed/cancelled booking is never represented as a valid
reservation simply because the Safe Meet status is confirmed.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.services.safe_meet_service import (
    get_authorized_safe_meet,
)
from app.services.safe_meet_reservation_consistency_service import (
    verify_linked_reservation_is_current,
)
from app.services.safe_meet_authorization_service import (
    SafeMeetAuthorizationError,
)


def read_authorized_safe_meet_booking_health(
    safe_meet_id,
    *,
    user_id,
    db=None,
    now=None,
):
    """Return a member-only current reservation health snapshot.

    Invalid reservations are a business state, not proof that
    consent never occurred. Database failures must propagate rather
    than being represented as a cancelled or missing booking.
    """
    if not isinstance(user_id, ObjectId):
        raise SafeMeetAuthorizationError(
            "Authenticated user identity required."
        )

    plan = get_authorized_safe_meet(
        safe_meet_id,
        user_id=user_id,
        db=db,
    )

    if not isinstance(plan, dict):
        raise SafeMeetAuthorizationError(
            "Safe Meet is unavailable."
        )

    recorded_at = (
        datetime.now(timezone.utc)
        if now is None
        else now
    )
    if not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required.")

    reservation_id = plan.get("reservation_id")
    health = {
        "safe_meet_id": str(plan["_id"]),
        "revision": plan["revision"],
        "meet_status": plan["status"],
        "reservation_id": reservation_id,
        "booking_health": "not_linked",
        "booking_verified": False,
        "booking_requires_attention": False,
        "checked_at": recorded_at.astimezone(timezone.utc).isoformat(),
    }

    if reservation_id is None:
        return health

    try:
        verify_linked_reservation_is_current(plan)
    except SafeMeetAuthorizationError:
        health["booking_health"] = "needs_attention"
        health["booking_requires_attention"] = True
        return health

    health["booking_health"] = "valid"
    health["booking_verified"] = True
    return health
