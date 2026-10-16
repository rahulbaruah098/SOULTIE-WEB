"""Bounded, manually dispatched Notification Celery tasks.

MongoDB owns delivery state. Redis task payloads contain no
Notification content, recipient details, or device credentials.
"""

from app.jobs.celery_app import celery_app
from app.repositories.notification_delivery_repository import (
    recover_expired_notification_delivery_leases,
)
from app.services.notification_delivery_executor import (
    execute_next_notification_delivery,
)


MAX_DELIVERY_BATCH_SIZE = 1


def _batch_size(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_DELIVERY_BATCH_SIZE
    ):
        raise ValueError("Invalid Notification batch size.")
    return value


@celery_app.task(
    name="xuoroni.notifications.process_due",
)
def process_due_notification_deliveries(max_items=1):
    batch_size = _batch_size(max_items)

    recovered = recover_expired_notification_delivery_leases()
    statuses = []

    for _ in range(batch_size):
        result = execute_next_notification_delivery()
        status = result["status"]

        if status == "idle":
            break

        if status not in {
            "delivered",
            "pending",
            "failed",
            "suppressed",
            "stale",
            "lease_expired",
        }:
            raise RuntimeError(
                "Unknown Notification delivery result."
            )

        statuses.append(status)

    return {
        "recovered": recovered,
        "processed": len(statuses),
        "statuses": statuses,
    }


@celery_app.task(
    name="xuoroni.notifications.recover_leases",
)
def recover_notification_leases():
    return {
        "recovered": recover_expired_notification_delivery_leases()
    }


# Scheduled reconciliation uses MongoDB state, never Redis task results.
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument
from app.extensions import mongo
from app.services.notification_delivery_service import (
    reconcile_notification_deliveries,
)


RECONCILIATION_STATE_ID = "notification-delivery-reconciliation"
RECONCILIATION_PAGE_SIZE = 25
RECONCILIATION_LEASE_SECONDS = 120


@celery_app.task(
    name="xuoroni.notifications.reconcile_page",
)
def reconcile_notification_delivery_page():
    """Repair one durable Notification page with crash-safe continuation.

    A MongoDB lease prevents concurrent reconciliation tasks.
    Cursor advances only after the page has been provisioned.
    """
    now = datetime.now(timezone.utc)
    token = uuid4().hex
    collection = mongo.db.notification_reconciliation_state

    collection.update_one(
        {"_id": RECONCILIATION_STATE_ID},
        {"$setOnInsert": {
            "lease_until": None,
            "lease_token": None,
            "cursor_created_at": None,
            "cursor_notification_id": None,
        }},
        upsert=True,
    )

    state = collection.find_one_and_update(
        {
            "_id": RECONCILIATION_STATE_ID,
            "$or": [
                {"lease_until": None},
                {"lease_until": {"$lte": now}},
            ],
        },
        {"$set": {
            "lease_until": now + timedelta(
                seconds=RECONCILIATION_LEASE_SECONDS
            ),
            "lease_token": token,
        }},
        return_document=ReturnDocument.AFTER,
    )

    if state is None:
        return {"status": "busy", "scanned": 0}

    fence = {
        "_id": RECONCILIATION_STATE_ID,
        "lease_token": token,
    }

    try:
        page = reconcile_notification_deliveries(
            limit=RECONCILIATION_PAGE_SIZE,
            after_created_at=state.get("cursor_created_at"),
            after_notification_id=state.get(
                "cursor_notification_id"
            ),
        )

        if not isinstance(page, dict):
            raise RuntimeError("Invalid reconciliation result")

        scanned = page.get("scanned")
        has_more = page.get("has_more")
        cursor = page.get("next_cursor")

        if (
            isinstance(scanned, bool)
            or not isinstance(scanned, int)
            or not 0 <= scanned <= RECONCILIATION_PAGE_SIZE
            or not isinstance(has_more, bool)
        ):
            raise RuntimeError("Invalid reconciliation page metadata")

        if has_more:
            if (
                not isinstance(cursor, dict)
                or not isinstance(cursor.get("created_at"), datetime)
                or cursor.get("notification_id") is None
            ):
                raise RuntimeError("Missing reconciliation cursor")
            next_created = cursor["created_at"]
            next_id = cursor["notification_id"]
        else:
            # Completed scan: next scheduled run starts a new cycle.
            next_created = None
            next_id = None

        updated = collection.update_one(
            fence,
            {"$set": {
                "cursor_created_at": next_created,
                "cursor_notification_id": next_id,
                "lease_until": None,
                "lease_token": None,
            }},
        )

        if updated.matched_count != 1:
            raise RuntimeError("Reconciliation lease ownership lost")

        return {
            "status": "continued" if has_more else "completed",
            "scanned": scanned,
            "has_more": has_more,
        }

    except Exception:
        # Never advance the cursor after an unsuccessful page.
        # A future task may safely repeat the page idempotently.
        collection.update_one(
            fence,
            {"$set": {
                "lease_until": None,
                "lease_token": None,
            }},
        )
        raise
