"""Pure Safe Meet lifecycle rules for Soultie Phase 2M.

Storage, conversation membership, café eligibility, and reservation
verification belong to the transaction-backed integration layer.
"""

from copy import deepcopy
from datetime import datetime, timezone

from bson import ObjectId


class SafeMeetLifecycleError(ValueError):
    """A lifecycle operation is invalid or not permitted."""


def _identity(value):
    if type(value) is not str or not ObjectId.is_valid(value):
        raise SafeMeetLifecycleError("Invalid participant identity.")
    return str(ObjectId(value))


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetLifecycleError("Timezone-aware timestamp required.")
    return value.astimezone(timezone.utc).isoformat()


def create_safe_meet_proposal(
    *,
    conversation_id,
    proposer_id,
    participant_ids,
    planned_at,
    venue_id=None,
    reservation_id=None,
    now,
):
    """Construct a proposal; does not write to MongoDB."""
    conversation_id = _identity(conversation_id)
    proposer_id = _identity(proposer_id)

    if not isinstance(participant_ids, (list, tuple)):
        raise SafeMeetLifecycleError("Exactly two participants required.")

    members = [_identity(item) for item in participant_ids]
    if len(members) != 2 or len(set(members)) != 2:
        raise SafeMeetLifecycleError("Exactly two unique participants required.")
    if proposer_id not in members:
        raise SafeMeetLifecycleError("Proposer must be a participant.")

    timestamp = _timestamp(now)
    planned = _timestamp(planned_at)
    if planned <= timestamp:
        raise SafeMeetLifecycleError("Meetup must be scheduled in the future.")

    venue = _identity(venue_id) if venue_id is not None else None
    reservation = (
        _identity(reservation_id)
        if reservation_id is not None else None
    )
    if reservation and not venue:
        raise SafeMeetLifecycleError(
            "Reservation reference requires a venue reference."
        )

    proposal = {
        "conversation_id": conversation_id,
        "proposer_id": proposer_id,
        "participant_ids": members,
        "planned_at": planned,
        "venue_id": venue,
        "reservation_id": reservation,
        "status": "proposed",
        "consent": {member: False for member in members},
        "completion_acknowledgements": {
            member: False for member in members
        },
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 1,
    }

    event = {
        "action": "propose",
        "actor_id": proposer_id,
        "from_status": None,
        "to_status": "proposed",
        "occurred_at": timestamp,
        "revision": 1,
    }
    return proposal, event


def apply_safe_meet_action(plan, *, actor_id, action, now):
    """Return updated plan and audit event, without persistence."""
    if not isinstance(plan, dict):
        raise SafeMeetLifecycleError("Invalid plan.")

    actor = _identity(actor_id)
    timestamp = _timestamp(now)
    result = deepcopy(plan)

    members = result.get("participant_ids")
    if (
        not isinstance(members, list)
        or len(members) != 2
        or len(set(members)) != 2
        or actor not in members
    ):
        raise SafeMeetLifecycleError("Actor is not a participant.")

    status = result.get("status")
    if action not in {"accept", "decline", "cancel", "complete"}:
        raise SafeMeetLifecycleError("Unsupported Safe Meet action.")
    if type(result.get("revision")) is not int or result["revision"] < 1:
        raise SafeMeetLifecycleError("Invalid plan revision.")

    before = status

    if action == "accept":
        if status != "proposed":
            raise SafeMeetLifecycleError("Proposal is not awaiting consent.")
        if result["consent"].get(actor) is not False:
            raise SafeMeetLifecycleError("Consent already recorded.")
        result["consent"][actor] = True
        if all(result["consent"].get(user) is True for user in members):
            result["status"] = "confirmed"

    elif action == "decline":
        if status != "proposed":
            raise SafeMeetLifecycleError("Only proposals may be declined.")
        result["status"] = "declined"

    elif action == "cancel":
        if status not in {"proposed", "confirmed"}:
            raise SafeMeetLifecycleError("Plan cannot be canceled.")
        result["status"] = "canceled"

    elif action == "complete":
        if status != "confirmed":
            raise SafeMeetLifecycleError("Only confirmed plans may complete.")
        if result["completion_acknowledgements"].get(actor) is not False:
            raise SafeMeetLifecycleError("Completion already acknowledged.")
        result["completion_acknowledgements"][actor] = True
        if all(
            result["completion_acknowledgements"].get(user) is True
            for user in members
        ):
            result["status"] = "completed"

    result["revision"] += 1
    result["updated_at"] = timestamp

    event = {
        "action": action,
        "actor_id": actor,
        "from_status": before,
        "to_status": result["status"],
        "occurred_at": timestamp,
        "revision": result["revision"],
    }
    return result, event