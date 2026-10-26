
"""Read-only, contact-scoped SOS inbox.

Does not deliver notifications or expose coordinates.
"""
from datetime import datetime, timezone
from bson import ObjectId
from app.services.safe_meet_sos_dispatch_guard import (
    authorize_sos_escalation,
)


def _identifier(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError("Invalid contact identifier.")


def list_trusted_contact_sos(
    *, database, contact_id, now, limit=20
):
    contact = _identifier(contact_id)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required.")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid page size.")

    moment = now.astimezone(timezone.utc)

    user = database.users.find_one({
        "_id": contact,
        "account_status": "active",
    }, {"_id": 1})
    if user is None:
        return []

    # Fetch only escalations designated for this exact contact.
    cursor = database.safe_meet_sos_escalations.find({
        "contact_id": contact,
        "delivery_enabled": False,
        "status": "pending_manual_review",
    }).sort([("created_at", -1), ("_id", -1)]).limit(200)

    inbox = []
    for entry in cursor:
        authorization = authorize_sos_escalation(
            database=database,
            escalation_id=entry["_id"],
            now=moment,
        )
        if not authorization.get("allowed"):
            continue
        if authorization.get("contact_id") != contact:
            continue

        event = database.safe_meet_sos_events.find_one({
            "_id": entry.get("sos_event_id"),
            "safe_meet_id": entry.get("safe_meet_id"),
            "owner_id": entry.get("owner_id"),
            "status": "recorded",
        }, {"_id": 1, "created_at": 1})
        if not event:
            continue

        created_at = event.get("created_at")
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(
                    tzinfo=timezone.utc
                )
            timestamp = created_at.isoformat()
        else:
            timestamp = None

        # SAFE_MEET_SOS_INBOX_ACK_STATUS
        acknowledgment = database.safe_meet_sos_acknowledgments.find_one(
            {
                "consent_revision": entry.get("consent_revision"),
                "sos_event_id": event["_id"],
                "contact_id": contact,
                "status": "acknowledged",
            },
            {"_id": 0, "acknowledged_at": 1},
        )

        acknowledged_at = None
        if acknowledgment:
            when = acknowledgment.get("acknowledged_at")
            if isinstance(when, datetime):
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                acknowledged_at = when.isoformat()

        inbox.append({
            "acknowledgment_status": (
                "acknowledged" if acknowledgment else "pending"
            ),
            "acknowledged_at": acknowledged_at,
            "sos_event_id": str(event["_id"]),
            "safe_meet_id": str(entry["safe_meet_id"]),
            "owner_id": str(entry["owner_id"]),
            "status": "pending_manual_review",
            "created_at": timestamp,
            "notification_delivered": False,
        })
        if len(inbox) >= limit:
            break

    return inbox
