"""Generate deterministic reminders for confirmed Safe Meets.

Pure scheduling logic. Persistence, FCM delivery and background
execution are handled separately.
"""

from datetime import datetime, timedelta, timezone
from bson import ObjectId


class SafeMeetReminderError(ValueError):
    pass


REMINDER_WINDOWS = (
    ("24h", timedelta(hours=24)),
    ("1h", timedelta(hours=1)),
)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetReminderError(
            "A timezone-aware datetime is required."
        )
    return value.astimezone(timezone.utc)


def build_safe_meet_reminders(plan, *, now):
    """Return future reminder jobs for both confirmed participants."""
    now = _utc(now)

    if not isinstance(plan, dict):
        raise SafeMeetReminderError("Invalid Safe Meet plan.")

    if plan.get("status") != "confirmed":
        return []

    if plan.get("booking_health") == "needs_attention":
        return []

    meet_id = plan.get("_id")
    participants = plan.get("participant_ids")
    planned_at = _utc(plan.get("planned_at"))
    revision = plan.get("revision")

    if (
        not isinstance(meet_id, ObjectId)
        or not isinstance(participants, list)
        or len(participants) != 2
        or any(
            type(user_id) is not str
            or not ObjectId.is_valid(user_id)
            for user_id in participants
        )
        or len(set(participants)) != 2
        or type(revision) is not int
        or revision < 1
    ):
        raise SafeMeetReminderError(
            "Invalid confirmed Safe Meet identifiers."
        )

    if planned_at <= now:
        return []

    reminders = []

    for window, offset in REMINDER_WINDOWS:
        due_at = planned_at - offset

        if due_at <= now:
            continue

        for recipient in participants:
            reminders.append({
                "idempotency_key": (
                    f"safe_meet_reminder:{meet_id}:"
                    f"{revision}:{window}:{recipient}"
                ),
                "safe_meet_id": str(meet_id),
                "recipient_id": recipient,
                "event_type": "safe_meet_reminder",
                "reminder_window": window,
                "meet_revision": revision,
                "scheduled_at": due_at,
                "delivery_status": "pending",
            })

    return reminders
