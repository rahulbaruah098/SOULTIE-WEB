"""Authorized transactional Safe Meet operations.

The HTTP layer must supply the authenticated user ID; a client must
never select the acting user. Plans do not create cafe reservations.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.repositories.safe_meet_repository import (
    SafeMeetConflictError,
    SafeMeetNotFoundError,
    create_safe_meet_record,
    get_safe_meet_record,
    list_safe_meet_events,
    transition_safe_meet_record,
)
from app.services.safe_meet_authorization_service import (
    SafeMeetAuthorizationError,
    prepare_authorized_safe_meet_proposal,
)


def _actor_id(value):
    if not isinstance(value, ObjectId):
        raise SafeMeetAuthorizationError(
            "An authenticated user identity is required."
        )
    return value


def _check_plan_access(plan, actor):
    from app.services.chat_service import get_my_conversation

    if not isinstance(plan, dict):
        raise SafeMeetAuthorizationError("Safe Meet is unavailable.")

    members = plan.get("participant_ids")
    if (
        not isinstance(members, list)
        or len(members) != 2
        or len(set(members)) != 2
        or str(actor) not in members
        or not isinstance(plan.get("conversation_id"), str)
    ):
        raise SafeMeetAuthorizationError("Safe Meet is unavailable.")

    # The chat service checks current user/profile eligibility
    # and conversation membership against the live chat record.
    get_my_conversation(
        user_id=actor,
        conversation_id=plan["conversation_id"],
    )

    # Explicit block enforcement, independent of chat authorization.
    from bson.errors import InvalidId
    from app.repositories.safety_repository import pair_is_blocked

    try:
        first, second = (ObjectId(member) for member in members)
    except (InvalidId, ValueError, TypeError):
        raise SafeMeetAuthorizationError(
            "Safe Meet is unavailable."
        ) from None

    if pair_is_blocked(first, second):
        raise SafeMeetAuthorizationError(
            "Safe Meet is unavailable."
        )

    return plan


def propose_safe_meet(
    *,
    user_id,
    conversation_id,
    planned_at,
    venue_id=None,
    reservation_id=None,
    now=None,
    db=None,
):
    """Authorize a proposed meetup, then atomically persist its audit."""
    actor = _actor_id(user_id)
    moment = now if now is not None else datetime.now(timezone.utc)

    plan, audit = prepare_authorized_safe_meet_proposal(
        user_id=actor,
        conversation_id=conversation_id,
        planned_at=planned_at,
        venue_id=venue_id,
        reservation_id=reservation_id,
        now=moment,
    )

    return create_safe_meet_record(plan, audit, db=db)


def get_authorized_safe_meet(
    safe_meet_id,
    *,
    user_id,
    db=None,
):
    """Read a plan only after verifying current conversation access."""
    actor = _actor_id(user_id)
    plan = get_safe_meet_record(safe_meet_id, db=db)
    return _check_plan_access(plan, actor)


def transition_authorized_safe_meet(
    safe_meet_id,
    *,
    user_id,
    action,
    expected_revision,
    now=None,
    db=None,
):
    """Reauthorize and atomically perform a revision-checked action."""
    actor = _actor_id(user_id)
    plan = get_authorized_safe_meet(
        safe_meet_id,
        user_id=actor,
        db=db,
    )

    if (
        type(expected_revision) is not int
        or expected_revision < 1
    ):
        raise ValueError("Expected revision must be positive.")

    if plan.get("revision") != expected_revision:
        raise SafeMeetConflictError(
            "Safe Meet changed. Refresh before retrying."
        )

    moment = now if now is not None else datetime.now(timezone.utc)

    # Recheck the booking before each consent acceptance.
    # Decline/cancel remain possible even if the booking has expired.
    if action == "accept" and plan.get("reservation_id") is not None:
        from app.services.safe_meet_reservation_consistency_service import (
            verify_linked_reservation_is_current,
        )
        verify_linked_reservation_is_current(plan)

    return transition_safe_meet_record(
        safe_meet_id,
        actor_id=str(actor),
        action=action,
        expected_revision=expected_revision,
        now=moment,
        db=db,
    )


def get_authorized_safe_meet_events(
    safe_meet_id,
    *,
    user_id,
    db=None,
):
    """Return audit events only for a currently authorized participant."""
    get_authorized_safe_meet(
        safe_meet_id,
        user_id=user_id,
        db=db,
    )
    return list_safe_meet_events(safe_meet_id, db=db)