"""Internal transactional Soultie cafe reservation lifecycle service.

Public HTTP routes must obtain actor_id exclusively from the verified
dating-user or cafe-partner authentication context.

Never trust actor_role or actor_id supplied in a client's JSON body.
This module does not authenticate HTTP requests.

The repository revalidates actor eligibility inside the MongoDB
transaction, takes the venue write fence, validates the transition,
and performs a status compare-and-set update.

Notifications must later be persisted as transactional outbox events
and delivered only after successful commit. Until then this service
must not be exposed through public lifecycle endpoints.
"""

from bson import ObjectId

from app.repositories.cafe_reservation_lifecycle_repository import (
    CafeReservationLifecycleDenied,
    transition_cafe_reservation_status,
)
from app.security.cafe_partner_access import (
    authorize_cafe_partner,
    CafePartnerAccessDenied,
)
from app.services.cafe_reservation_transaction_service import (
    run_cafe_reservation_transaction,
)


class CafeReservationLifecycleServiceDenied(PermissionError):
    """The requested lifecycle actor or action is invalid."""


_ALLOWED = {
    "customer": frozenset({"cancelled"}),
    "cafe_partner": frozenset({
        "confirmed",
        "rejected",
        "cancelled",
        "completed",
        "no_show",
    }),
}


def _identity(value, label):
    if (
        not isinstance(value, str)
        or not ObjectId.is_valid(value)
    ):
        raise CafeReservationLifecycleServiceDenied(
            f"Invalid {label}."
        )
    return str(ObjectId(value))


def change_cafe_reservation_status(
    *,
    reservation_id,
    actor_id,
    actor_role,
    decision,
    venue_id=None,
):
    """Apply one role-restricted reservation transition atomically.

    A caller must already have verified the user or cafe session.

    venue_id is required for cafe partners and must be taken from
    the authenticated cafe session, not a client-supplied field.

    The repository separately verifies the booking belongs to
    the actor or cafe before mutating it.
    """
    reservation_id = _identity(
        reservation_id, "reservation ID"
    )
    actor_id = _identity(actor_id, "actor ID")

    if (
        not isinstance(actor_role, str)
        or actor_role not in _ALLOWED
        or not isinstance(decision, str)
        or decision not in _ALLOWED[actor_role]
    ):
        raise CafeReservationLifecycleServiceDenied(
            "Reservation lifecycle action is not permitted."
        )

    if actor_role == "cafe_partner":
        venue_id = _identity(venue_id, "venue ID")

        context = authorize_cafe_partner(
            partner_id=actor_id,
            venue_id=venue_id,
        )

        if (
            not isinstance(context, dict)
            or context.get("account_type") != "cafe_partner"
            or context.get("partner_id") != actor_id
            or context.get("venue_id") != venue_id
        ):
            raise CafeReservationLifecycleServiceDenied(
                "Cafe partner authorization failed."
            )

    elif venue_id is not None:
        raise CafeReservationLifecycleServiceDenied(
            "Customer lifecycle actions cannot supply a venue ID."
        )

    def operation(session):
        if (
            session is None
            or getattr(session, "in_transaction", False) is not True
        ):
            raise CafeReservationLifecycleServiceDenied(
                "An active MongoDB transaction is required."
            )

        return transition_cafe_reservation_status(
            reservation_id=reservation_id,
            actor_id=actor_id,
            actor_role=actor_role,
            decision=decision,
            session=session,
        )

    return run_cafe_reservation_transaction(
        operation=operation,
    )
