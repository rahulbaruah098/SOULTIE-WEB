"""Indexes for the durable Safe Meet notification outbox.

The database is provided explicitly by the caller. Indexes are
created idempotently and do not dispatch notifications.
"""

from pymongo import ASCENDING


def ensure_safe_meet_outbox_indexes(database):
    if database is None:
        raise ValueError("MongoDB database required.")

    collection = database.safe_meet_notification_outbox

    unique_name = collection.create_index(
        [("idempotency_key", ASCENDING)],
        unique=True,
        name="unique_safe_meet_notification_key",
    )

    pending_name = collection.create_index(
        [
            ("delivery_status", ASCENDING),
            ("next_attempt_at", ASCENDING),
            ("created_at", ASCENDING),
        ],
        name="safe_meet_outbox_pending_due",
    )

    recipient_name = collection.create_index(
        [
            ("recipient_id", ASCENDING),
            ("created_at", ASCENDING),
        ],
        name="safe_meet_outbox_recipient_history",
    )

    return {
        "unique": unique_name,
        "pending_due": pending_name,
        "recipient_history": recipient_name,
    }
