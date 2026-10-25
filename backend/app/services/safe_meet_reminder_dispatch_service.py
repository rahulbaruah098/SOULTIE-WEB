"""Atomically queue due Safe Meet reminders in the notification outbox.

This service does not send push notifications.
"""

from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError


def _parse_time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if value.tzinfo is None:
            return None
    elif isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc)


def queue_due_safe_meet_reminders(*, database, now, limit=25):
    """Queue eligible reminders with a transactional meeting write fence."""
    moment = _parse_time(now)
    if moment is None:
        raise ValueError("Valid aware time required.")
    if database is None:
        raise ValueError("MongoDB database required.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid batch limit.")

    reminders = database.safe_meet_reminders
    plans = database.safe_meet_plans
    outbox = database.safe_meet_notification_outbox

    candidates = list(
        reminders.find({
            "delivery_status": "pending",
            "scheduled_at": {"$lte": moment},
        }).sort("scheduled_at", 1).limit(limit)
    )

    results = {"queued": 0, "skipped": 0}

    for candidate in candidates:
        try:
            with database.client.start_session() as session:
                with session.start_transaction():
                    reminder = reminders.find_one(
                        {
                            "_id": candidate["_id"],
                            "delivery_status": "pending",
                            "scheduled_at": {"$lte": moment},
                        },
                        session=session,
                    )
                    if reminder is None:
                        results["skipped"] += 1
                        continue

                    meet_id = reminder.get("safe_meet_id")
                    if (
                        type(meet_id) is not str
                        or not ObjectId.is_valid(meet_id)
                    ):
                        results["skipped"] += 1
                        continue

                    plan = plans.find_one(
                        {"_id": ObjectId(meet_id)},
                        session=session,
                    )

                    due = _parse_time(reminder.get("scheduled_at"))
                    planned = _parse_time(
                        plan.get("planned_at") if plan else None
                    )
                    window = reminder.get("reminder_window")
                    recipient = reminder.get("recipient_id")
                    revision = reminder.get("meet_revision")

                    offset = {
                        "24h": timedelta(hours=24),
                        "1h": timedelta(hours=1),
                    }.get(window)

                    eligible = (
                        isinstance(plan, dict)
                        and plan.get("status") == "confirmed"
                        and plan.get("booking_health") != "needs_attention"
                        and type(revision) is int
                        and plan.get("revision") == revision
                        and type(recipient) is str
                        and ObjectId.is_valid(recipient)
                        and recipient in plan.get("participant_ids", [])
                        and due is not None
                        and due <= moment
                        and planned is not None
                        and planned > moment
                        and offset is not None
                        and abs(
                            (planned - offset - due).total_seconds()
                        ) < 0.001
                    )

                    if not eligible:
                        results["skipped"] += 1
                        continue

                    conversation = plan.get("conversation_id")
                    if (
                        type(conversation) is not str
                        or not ObjectId.is_valid(conversation)
                    ):
                        results["skipped"] += 1
                        continue

                    # A write to the plan ensures that a concurrent
                    # lifecycle transaction updating this same plan
                    # conflicts rather than silently interleaving.
                    fence = plans.update_one(
                        {
                            "_id": plan["_id"],
                            "status": "confirmed",
                            "revision": revision,
                        },
                        {"$inc": {"reminder_dispatch_fence": 1}},
                        session=session,
                    )
                    if fence.modified_count != 1:
                        raise RuntimeError("Meeting changed during dispatch.")

                    key = reminder.get("idempotency_key")
                    if type(key) is not str or not key:
                        raise ValueError("Reminder idempotency key missing.")

                    record = {
                        "idempotency_key": key,
                        "event_type": "safe_meet_reminder",
                        "safe_meet_id": meet_id,
                        "conversation_id": conversation,
                        "recipient_id": recipient,
                        "actor_id": None,
                        "revision": revision,
                        "meet_status": "confirmed",
                        "reminder_window": window,
                        "reminder_id": str(reminder["_id"]),
                        "delivery_status": "pending",
                        "attempt_count": 0,
                        "created_at": moment,
                        "updated_at": moment,
                        "next_attempt_at": moment,
                        "sent_at": None,
                        "last_error": None,
                    }

                    outbox.insert_one(record, session=session)

                    changed = reminders.update_one(
                        {
                            "_id": reminder["_id"],
                            "delivery_status": "pending",
                        },
                        {
                            "$set": {
                                "delivery_status": "queued",
                                "queued_at": moment,
                                "updated_at": moment,
                            }
                        },
                        session=session,
                    )
                    if changed.modified_count != 1:
                        raise RuntimeError("Reminder was claimed concurrently.")

                    results["queued"] += 1

        except DuplicateKeyError:
            # A duplicate key never creates another outbox message.
            results["skipped"] += 1

    return results
