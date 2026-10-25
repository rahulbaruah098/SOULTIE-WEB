"""Safe Meet reminder indexes."""
from pymongo import ASCENDING

def ensure_safe_meet_reminder_indexes(database):
    collection = database.safe_meet_reminders
    return (
        collection.create_index(
            [("idempotency_key", ASCENDING)],
            unique=True,
            name="safe_meet_reminder_unique_key",
        ),
        collection.create_index(
            [("delivery_status", ASCENDING), ("scheduled_at", ASCENDING)],
            name="safe_meet_reminder_due",
        ),
        collection.create_index(
            [("safe_meet_id", ASCENDING), ("meet_revision", ASCENDING)],
            name="safe_meet_reminder_revision",
        ),
    )
