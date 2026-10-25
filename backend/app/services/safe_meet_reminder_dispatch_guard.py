"""Fail-closed eligibility checks for Safe Meet reminders.

No external delivery occurs here. Call immediately before dispatch.
"""

from datetime import datetime, timezone
from bson import ObjectId


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        # MongoDB BSON datetimes are returned as naive UTC by default.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def reminder_is_eligible(*, database, reminder_id, now):
    """Check current reminder and authoritative Safe Meet state."""
    if database is None:
        raise ValueError("Database required.")
    now = _utc(now)
    if now is None:
        raise ValueError("Valid check time required.")
    if not isinstance(reminder_id, ObjectId):
        return False

    reminder = database.safe_meet_reminders.find_one(
        {"_id": reminder_id}
    )
    if not isinstance(reminder, dict):
        return False
    if reminder.get("delivery_status") != "pending":
        return False

    due = _utc(reminder.get("scheduled_at"))
    if due is None or due > now:
        return False

    meet_id = reminder.get("safe_meet_id")
    if not isinstance(meet_id, str) or not ObjectId.is_valid(meet_id):
        return False

    meet = database.safe_meet_plans.find_one(
        {"_id": ObjectId(meet_id)}
    )
    if not isinstance(meet, dict):
        return False

    if meet.get("status") != "confirmed":
        return False
    if meet.get("booking_health") == "needs_attention":
        return False

    revision = reminder.get("meet_revision")
    if type(revision) is not int or meet.get("revision") != revision:
        return False

    recipient = reminder.get("recipient_id")
    participants = meet.get("participant_ids")
    if (
        type(recipient) is not str
        or not isinstance(participants, list)
        or recipient not in participants
    ):
        return False

    planned = _utc(meet.get("planned_at"))
    if planned is None or planned <= now:
        return False

    window = reminder.get("reminder_window")
    if window not in ("24h", "1h"):
        return False

    from datetime import timedelta
    offset = timedelta(hours=24 if window == "24h" else 1)
    expected_due = planned - offset
    if abs((expected_due - due).total_seconds()) > 0.001:
        return False

    return True
