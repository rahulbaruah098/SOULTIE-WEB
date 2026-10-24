"""Safe Meet notification intent generation.

Pure event mapping; does not deliver messages or write to MongoDB.
"""

from bson import ObjectId


class SafeMeetNotificationIntentError(ValueError):
    pass


EVENT_TYPES = {
    "propose": "safe_meet_proposed",
    "accept": "safe_meet_consent_updated",
    "decline": "safe_meet_declined",
    "cancel": "safe_meet_cancelled",
    "complete": "safe_meet_completion_updated",
}


def build_safe_meet_notification_intents(plan, event):
    if not isinstance(plan, dict) or not isinstance(event, dict):
        raise SafeMeetNotificationIntentError("Invalid notification data.")

    members = plan.get("participant_ids")
    actor = event.get("actor_id")
    action = event.get("action")
    revision = event.get("revision")
    meet_id = plan.get("_id")
    conversation_id = plan.get("conversation_id")

    if (
        not isinstance(members, list)
        or len(members) != 2
        or any(
            type(item) is not str or not ObjectId.is_valid(item)
            for item in members
        )
        or len(set(members)) != 2
        or actor not in members
        or type(action) is not str
        or action not in EVENT_TYPES
        or type(revision) is not int
        or revision < 1
        or not isinstance(meet_id, ObjectId)
        or type(conversation_id) is not str
        or not ObjectId.is_valid(conversation_id)
        or plan.get("revision") != revision
        or event.get("to_status") != plan.get("status")
    ):
        raise SafeMeetNotificationIntentError(
            "Safe Meet event validation failed."
        )

    recipient = next(member for member in members if member != actor)

    return [{
        "idempotency_key": (
            f"safe_meet:{meet_id}:{revision}:{recipient}"
        ),
        "event_type": EVENT_TYPES[action],
        "safe_meet_id": str(meet_id),
        "conversation_id": conversation_id,
        "recipient_id": recipient,
        "actor_id": actor,
        "revision": revision,
        "meet_status": plan["status"],
        "delivery_status": "pending",
    }]
