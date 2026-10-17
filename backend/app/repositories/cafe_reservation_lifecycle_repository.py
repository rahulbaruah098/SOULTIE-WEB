"""Transactional, actor-scoped cafe reservation lifecycle persistence.

Internal repository only. A public API MUST authenticate the actor and
derive actor_id from the corresponding verified dating-user or cafe
partner session, never from the request body.

All status mutations serialize through the existing venue write fence
and use the same MongoDB transaction session. Status compare-and-set
prevents stale transitions.

No notifications or external side effects occur inside this module.
A transactional outbox is required before public lifecycle release.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.extensions import mongo
from app.repositories.cafe_booking_outbox_repository import (
    record_cafe_booking_event,
)
from app.repositories.cafe_reservation_fence_repository import (
    acquire_venue_reservation_fence,
)
from app.schemas.reservation import (
    validate_reservation_transition,
    ReservationTransitionError,
)


class CafeReservationLifecycleDenied(PermissionError):
    """Actor, venue or reservation is not authorized."""


class CafeReservationLifecycleConflict(ValueError):
    """Status transition or concurrent update is unavailable."""


_ROLE_DECISIONS = {
    "customer": frozenset({"cancelled"}),
    "cafe_partner": frozenset({
        "confirmed",
        "rejected",
        "cancelled",
        "completed",
        "no_show",
    }),
}


def _id(value, label):
    if (
        not isinstance(value, str)
        or not ObjectId.is_valid(value)
    ):
        raise CafeReservationLifecycleDenied(
            f"Invalid {label}."
        )
    return ObjectId(value)


def _utc_now(value):
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CafeReservationLifecycleDenied(
            "Timezone-aware lifecycle timestamp required."
        )
    return value.astimezone(timezone.utc)


def transition_cafe_reservation_status(
    *,
    reservation_id,
    actor_id,
    actor_role,
    decision,
    session,
    now=None,
):
    """Atomically transition one reservation for an authorized actor.

    This is one operation inside an already active MongoDB transaction.
    The caller must commit that transaction before reporting success.
    """
    reservation_oid = _id(reservation_id, "reservation ID")
    actor_oid = _id(actor_id, "actor ID")

    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise CafeReservationLifecycleDenied(
            "Active MongoDB transaction required."
        )

    if (
        not isinstance(actor_role, str)
        or actor_role not in _ROLE_DECISIONS
        or not isinstance(decision, str)
        or decision not in _ROLE_DECISIONS[actor_role]
    ):
        raise CafeReservationLifecycleDenied(
            "This actor cannot perform the requested action."
        )

    timestamp = _utc_now(now)

    reservation = mongo.db.reservations.find_one(
        {"_id": reservation_oid},
        session=session,
    )

    if (
        not isinstance(reservation, dict)
        or reservation.get("_id") != reservation_oid
        or not isinstance(reservation.get("venue_id"), ObjectId)
        or not isinstance(reservation.get("customer_id"), ObjectId)
    ):
        raise CafeReservationLifecycleDenied(
            "Reservation is not available."
        )

    venue_oid = reservation["venue_id"]
    customer_oid = reservation["customer_id"]

    if actor_role == "customer":
        if actor_oid != customer_oid:
            raise CafeReservationLifecycleDenied(
                "Customer does not own this reservation."
            )

        customer = mongo.db.users.find_one(
            {
                "_id": actor_oid,
                "account_status": "active",
            },
            session=session,
        )

        if (
            not isinstance(customer, dict)
            or customer.get("_id") != actor_oid
            or customer.get("account_status") != "active"
            or customer.get("account_type") not in (
                None, "user", "dating_user"
            )
        ):
            raise CafeReservationLifecycleDenied(
                "Customer is not eligible."
            )
    else:
        owner = mongo.db.cafe_partner_accounts.find_one(
            {
                "_id": actor_oid,
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "venue_id": venue_oid,
            },
            session=session,
        )

        if (
            not isinstance(owner, dict)
            or owner.get("_id") != actor_oid
            or owner.get("account_type") != "cafe_partner"
            or owner.get("status") != "approved"
            or owner.get("email_verified") is not True
            or owner.get("venue_id") != venue_oid
        ):
            raise CafeReservationLifecycleDenied(
                "Cafe partner is not authorized for this venue."
            )

        venue = mongo.db.venues.find_one(
            {
                "_id": venue_oid,
                "owner_id": str(actor_oid),
                "status": "approved",
                "active": True,
            },
            session=session,
        )

        if (
            not isinstance(venue, dict)
            or venue.get("_id") != venue_oid
            or venue.get("owner_id") != str(actor_oid)
            or venue.get("status") != "approved"
            or venue.get("active") is not True
        ):
            raise CafeReservationLifecycleDenied(
                "Venue ownership cannot be verified."
            )

    current = reservation.get("status")

    try:
        validate_reservation_transition(current, decision)
    except ReservationTransitionError as exc:
        raise CafeReservationLifecycleConflict(
            "Reservation transition is not permitted."
        ) from exc

    if decision in {"completed", "no_show"}:
        start = reservation.get("start_at")
        if (
            not isinstance(start, datetime)
            or start.tzinfo is None
            or start.utcoffset() is None
            or timestamp < start.astimezone(timezone.utc)
        ):
            raise CafeReservationLifecycleConflict(
                "Reservation has not started."
            )

    acquire_venue_reservation_fence(
        venue_id=str(venue_oid),
        session=session,
    )

    result = mongo.db.reservations.update_one(
        {
            "_id": reservation_oid,
            "venue_id": venue_oid,
            "customer_id": customer_oid,
            "status": current,
        },
        {
            "$set": {
                "status": decision,
                "updated_at": timestamp,
            }
        },
        session=session,
    )

    if (
        result is None
        or result.matched_count != 1
        or result.modified_count != 1
    ):
        raise CafeReservationLifecycleConflict(
            "Reservation changed during transition."
        )

    # Persist the lifecycle event in the SAME transaction
    # as the successful compare-and-set reservation update.
    # Any outbox failure must abort the entire transaction.
    record_cafe_booking_event(
        reservation_id=str(reservation_oid),
        venue_id=str(venue_oid),
        customer_id=str(customer_oid),
        event_type=f"booking_{decision}",
        reservation_status=decision,
        session=session,
    )

    return {
        "reservation_id": str(reservation_oid),
        "venue_id": str(venue_oid),
        "customer_id": str(customer_oid),
        "previous_status": current,
        "status": decision,
    }
