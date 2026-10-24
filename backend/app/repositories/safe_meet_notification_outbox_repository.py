"""Safe Meet transactional notification outbox.

Call only inside an existing MongoDB transaction.
No FCM, email or other notification delivery occurs here.
"""

from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.services.safe_meet_notification_intent_service import (
    build_safe_meet_notification_intents,
)


class SafeMeetOutboxError(RuntimeError):
    pass


class SafeMeetOutboxConflictError(SafeMeetOutboxError):
    pass


def enqueue_safe_meet_notification_intents(
    plan, event, *, database, session
):
    if database is None or session is None:
        raise SafeMeetOutboxError(
            "Database and transaction session required."
        )

    intents = build_safe_meet_notification_intents(plan, event)
    if len(intents) != 1:
        raise SafeMeetOutboxError(
            "Expected exactly one notification recipient."
        )

    timestamp = datetime.now(timezone.utc)
    record = {
        **intents[0],
        "created_at": timestamp,
        "updated_at": timestamp,
        "attempt_count": 0,
        "next_attempt_at": timestamp,
        "sent_at": None,
        "last_error": None,
    }

    try:
        result = database.safe_meet_notification_outbox.insert_one(
            record, session=session
        )
    except DuplicateKeyError as exc:
        raise SafeMeetOutboxConflictError(
            "Notification event already queued."
        ) from exc

    return [result.inserted_id]
