
"""Fail-closed authorization for SOS trusted-contact escalation.

This module never sends notifications and never exposes coordinates.
"""
from datetime import datetime, timezone
from bson import ObjectId


def _utc(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required.")
    return now.astimezone(timezone.utc)


def authorize_sos_escalation(*, database, escalation_id, now):
    moment = _utc(now)

    if isinstance(escalation_id, ObjectId):
        item_id = escalation_id
    elif type(escalation_id) is str and ObjectId.is_valid(escalation_id):
        item_id = ObjectId(escalation_id)
    else:
        return {"allowed": False, "reason": "invalid_escalation"}

    escalation = database.safe_meet_sos_escalations.find_one({"_id": item_id})
    if not escalation:
        return {"allowed": False, "reason": "missing_escalation"}

    if escalation.get("status") != "pending_manual_review":
        return {"allowed": False, "reason": "inactive_escalation"}

    if escalation.get("delivery_enabled") is not False:
        return {"allowed": False, "reason": "delivery_policy_mismatch"}

    meeting = escalation.get("safe_meet_id")
    owner = escalation.get("owner_id")
    contact = escalation.get("contact_id")
    revision = escalation.get("consent_revision")

    if not all(isinstance(item, ObjectId) for item in (meeting, owner, contact)):
        return {"allowed": False, "reason": "invalid_identity"}
    if type(revision) is not int or revision < 1:
        return {"allowed": False, "reason": "invalid_revision"}

    event = database.safe_meet_sos_events.find_one({
        "_id": escalation.get("sos_event_id"),
        "safe_meet_id": meeting,
        "owner_id": owner,
        "status": "recorded",
    })
    if not event:
        return {"allowed": False, "reason": "missing_sos_event"}

    plan = database.safe_meet_plans.find_one({
        "_id": meeting,
        "status": "confirmed",
        "participant_ids": str(owner),
    }, {"_id": 1})
    if not plan:
        return {"allowed": False, "reason": "meeting_unavailable"}

    consent = database.safe_meet_contact_consents.find_one({
        "safe_meet_id": meeting,
        "owner_id": owner,
        "contact_id": contact,
        "revision": revision,
        "status": "active",
        "expires_at": {"$gt": moment},
    }, {"_id": 1})
    if not consent:
        return {"allowed": False, "reason": "consent_unavailable"}

    acceptance = database.safe_meet_contact_acceptances.find_one({
        "safe_meet_id": meeting,
        "owner_id": owner,
        "contact_id": contact,
        "invitation_revision": revision,
        "decision": "accepted",
    }, {"_id": 1})
    if not acceptance:
        return {"allowed": False, "reason": "acceptance_unavailable"}

    user = database.users.find_one({
        "_id": contact,
        "account_status": "active",
    }, {"_id": 1})
    if not user:
        return {"allowed": False, "reason": "contact_unavailable"}

    return {
        "allowed": True,
        "reason": "authorized_for_simulation",
        "contact_id": contact,
        "consent_revision": revision,
    }
