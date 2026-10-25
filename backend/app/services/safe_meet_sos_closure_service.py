
"""Owner-authorized SOS incident closure.

Closure is a user action, not emergency-service confirmation.
Incident history remains stored for audit and retention controls.
"""
from datetime import datetime, timezone
from bson import ObjectId


class SOSClosureUnavailable(ValueError):
    pass


def close_owner_sos(
    *, database, safe_meet_id, sos_event_id, owner_id, now
):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SOSClosureUnavailable("SOS unavailable.")

    try:
        meeting = ObjectId(str(safe_meet_id))
        event = ObjectId(str(sos_event_id))
        owner = ObjectId(str(owner_id))
    except (TypeError, ValueError):
        raise SOSClosureUnavailable("SOS unavailable.")

    moment = now.astimezone(timezone.utc)

    with database.client.start_session() as session:
        with session.start_transaction():
            actor = database.users.find_one(
                {"_id": owner, "account_status": "active"},
                {"_id": 1},
                session=session,
            )
            plan = database.safe_meet_plans.find_one(
                {
                    "_id": meeting,
                    "participant_ids": str(owner),
                },
                {"_id": 1},
                session=session,
            )
            if actor is None or plan is None:
                raise SOSClosureUnavailable("SOS unavailable.")

            current = database.safe_meet_sos_events.find_one(
                {
                    "_id": event,
                    "safe_meet_id": meeting,
                    "owner_id": owner,
                },
                {"status": 1},
                session=session,
            )
            if current is None:
                raise SOSClosureUnavailable("SOS unavailable.")

            if current.get("status") == "closed":
                return {
                    "sos_event_id": str(event),
                    "status": "closed",
                    "replayed": True,
                    "emergency_dispatch_confirmed": False,
                }

            if current.get("status") != "recorded":
                raise SOSClosureUnavailable("SOS unavailable.")

            result = database.safe_meet_sos_events.update_one(
                {
                    "_id": event,
                    "safe_meet_id": meeting,
                    "owner_id": owner,
                    "status": "recorded",
                },
                {
                    "$set": {
                        "status": "closed",
                        "closed_at": moment,
                        "closed_by": owner,
                        "closure_type": "owner_closed_in_app",
                    }
                },
                session=session,
            )
            if result.modified_count != 1:
                raise SOSClosureUnavailable("SOS unavailable.")

            database.safe_meet_sos_escalations.update_many(
                {
                    "sos_event_id": event,
                    "safe_meet_id": meeting,
                    "owner_id": owner,
                    "delivery_enabled": False,
                    "status": "pending_manual_review",
                },
                {
                    "$set": {
                        "status": "suppressed",
                        "suppression_reason": "owner_closed_sos",
                        "suppressed_at": moment,
                    }
                },
                session=session,
            )

    return {
        "sos_event_id": str(event),
        "status": "closed",
        "replayed": False,
        "emergency_dispatch_confirmed": False,
    }
