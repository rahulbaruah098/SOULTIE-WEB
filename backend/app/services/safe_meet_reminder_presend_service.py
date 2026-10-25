"""Fail-closed pre-send validation for queued Safe Meet reminders."""

from datetime import datetime, timedelta, timezone
from bson import ObjectId


def _utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if value.tzinfo is None:
            return None
    elif isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc)


def reminder_can_send(*, database, outbox_item, now=None):
    if database is None or not isinstance(outbox_item, dict):
        return False

    if outbox_item.get("event_type") != "safe_meet_reminder":
        return False

    moment = _utc(now or datetime.now(timezone.utc))
    if moment is None:
        return False

    meet_id = outbox_item.get("safe_meet_id")
    reminder_id = outbox_item.get("reminder_id")

    if not all(
        isinstance(value, str) and ObjectId.is_valid(value)
        for value in (meet_id, reminder_id)
    ):
        return False

    plan = database.safe_meet_plans.find_one({
        "_id": ObjectId(meet_id),
    })
    reminder = database.safe_meet_reminders.find_one({
        "_id": ObjectId(reminder_id),
    })
    if not isinstance(plan, dict) or not isinstance(reminder, dict):
        return False

    recipient = outbox_item.get("recipient_id")
    revision = outbox_item.get("revision")

    if (
        plan.get("status") != "confirmed"
        or plan.get("booking_health") == "needs_attention"
        or type(revision) is not int
        or plan.get("revision") != revision
        or reminder.get("meet_revision") != revision
        or reminder.get("safe_meet_id") != meet_id
        or reminder.get("recipient_id") != recipient
        or recipient not in plan.get("participant_ids", [])
        or reminder.get("idempotency_key") !=
            outbox_item.get("idempotency_key")
        or reminder.get("delivery_status") != "queued"
        or reminder.get("reminder_window") !=
            outbox_item.get("reminder_window")
    ):
        return False

    planned = _utc(plan.get("planned_at"))
    scheduled = _utc(reminder.get("scheduled_at"))

    if planned is None or scheduled is None:
        return False
    if not scheduled <= moment < planned:
        return False

    hours = {"24h": 24, "1h": 1}.get(
        reminder.get("reminder_window")
    )
    if hours is None:
        return False

    expected = planned - timedelta(hours=hours)
    return abs((scheduled - expected).total_seconds()) < 0.001


class StaleSafeMeetReminder(RuntimeError):
    """Notification is permanently ineligible for delivery."""
    pass
