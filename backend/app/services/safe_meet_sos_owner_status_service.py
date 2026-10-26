
"""Minimal, owner-only SOS acknowledgment status."""
from datetime import datetime, timezone
from bson import ObjectId

from app.services.safe_meet_sos_dispatch_guard import (
    authorize_sos_escalation,
)


class OwnerSOSUnavailable(ValueError):
    pass


def get_owner_sos_acknowledgment_status(
    *, database, safe_meet_id, sos_event_id, owner_id, now
):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise OwnerSOSUnavailable("SOS unavailable.")

    try:
        meeting = ObjectId(str(safe_meet_id))
        event_id = ObjectId(str(sos_event_id))
        owner = ObjectId(str(owner_id))
    except (TypeError, ValueError):
        raise OwnerSOSUnavailable("SOS unavailable.")

    event = database.safe_meet_sos_events.find_one({
        "_id": event_id,
        "safe_meet_id": meeting,
        "owner_id": owner,
        "status": {"$in": ["recorded", "closed"]},
    }, {"_id": 1, "status": 1})

    plan = database.safe_meet_plans.find_one({
        "_id": meeting,
        "participant_ids": str(owner),
        "status": "confirmed",
    }, {"_id": 1})

    user = database.users.find_one({
        "_id": owner,
        "account_status": "active",
    }, {"_id": 1})

    if not event or not plan or not user:
        raise OwnerSOSUnavailable("SOS unavailable.")

    # SAFE_MEET_OWNER_CLOSED_STATUS_V2
    if event.get("status") == "closed":
        return {
            "sos_event_id": str(event_id),
            "acknowledgment_status": "closed",
            "incident_status": "closed",
            "acknowledged_contact_count": 0,
            "eligible_contact_count": 0,
            "notification_delivered": False,
            "emergency_dispatch_confirmed": False,
        }


    moment = now.astimezone(timezone.utc)
    eligible = 0
    acknowledged = 0

    cursor = database.safe_meet_sos_escalations.find({
        "sos_event_id": event_id,
        "safe_meet_id": meeting,
        "owner_id": owner,
        "status": "pending_manual_review",
        "delivery_enabled": False,
    }, {"_id": 1, "contact_id": 1, "consent_revision": 1})

    for escalation in cursor:
        approval = authorize_sos_escalation(
            database=database,
            escalation_id=escalation["_id"],
            now=moment,
        )
        if not approval.get("allowed"):
            continue

        if approval.get("contact_id") != escalation.get("contact_id"):
            continue

        eligible += 1

        acknowledgment = database.safe_meet_sos_acknowledgments.find_one({
            "sos_event_id": event_id,
            "contact_id": escalation["contact_id"],
            "consent_revision": escalation.get("consent_revision"),
            "status": "acknowledged",
        }, {"_id": 1})

        if acknowledgment:
            acknowledged += 1

    return {
        "sos_event_id": str(event_id),
        "acknowledgment_status": (
            "acknowledged" if acknowledged
            else "pending" if eligible else "no_active_contact"
        ),
        "acknowledged_contact_count": acknowledged,
        "eligible_contact_count": eligible,
        "notification_delivered": False,
        "emergency_dispatch_confirmed": False,
    }
