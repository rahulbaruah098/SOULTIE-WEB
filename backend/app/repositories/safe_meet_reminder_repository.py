"""Transactional scheduling and cancellation of Safe Meet reminders.

Call from a MongoDB transaction owned by the Safe Meet lifecycle.
Dispatching and delivery are separate responsibilities.
"""

from datetime import datetime, timezone

from app.services.safe_meet_reminder_schedule_service import (
    build_safe_meet_reminders,
)


def sync_safe_meet_reminders(plan, *, database, session, now):
    """Schedule current revision; invalidate obsolete pending work."""
    if database is None or session is None:
        raise ValueError("Database and transaction session required.")
    if not isinstance(plan, dict) or plan.get("_id") is None:
        raise ValueError("Safe Meet plan required.")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware now required.")

    now = now.astimezone(timezone.utc)
    collection = database.safe_meet_reminders
    meet_id = str(plan["_id"])

    raw_time = plan.get("planned_at")
    if isinstance(raw_time, str):
        parsed = datetime.fromisoformat(
            raw_time.replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            raise ValueError("Stored meeting timestamp requires timezone.")
        plan = {**plan, "planned_at": parsed}
    elif isinstance(raw_time, datetime) and raw_time.tzinfo is None:
        plan = {
            **plan,
            "planned_at": raw_time.replace(tzinfo=timezone.utc),
        }

    jobs = build_safe_meet_reminders(plan, now=now)
    desired_keys = [job["idempotency_key"] for job in jobs]

    # Cancellation, changed revision or no-longer-due jobs invalidate
    # unsent reminders. Sent records are retained for audit.
    invalidated = collection.update_many(
        {
            "safe_meet_id": meet_id,
            "delivery_status": {
                "$in": ["pending", "processing"]
            },
            "idempotency_key": {"$nin": desired_keys},
        },
        {
            "$set": {
                "delivery_status": "cancelled",
                "cancelled_at": now,
                "updated_at": now,
            }
        },
        session=session,
    ).modified_count

    inserted = 0

    for job in jobs:
        record = {
            **job,
            "created_at": now,
            "updated_at": now,
            "attempt_count": 0,
        }
        result = collection.update_one(
            {"idempotency_key": job["idempotency_key"]},
            {"$setOnInsert": record},
            upsert=True,
            session=session,
        )
        if result.upserted_id is not None:
            inserted += 1

    return {
        "scheduled": inserted,
        "invalidated": invalidated,
        "eligible": len(jobs),
    }
