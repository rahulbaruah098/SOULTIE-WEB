
"""Authenticated trusted-contact acknowledgment.

An acknowledgment means a contact viewed an SOS in the app.
It is not proof of notification delivery or emergency response.
"""
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError
from app.services.safe_meet_sos_dispatch_guard import (
    authorize_sos_escalation,
)


class SOSAcknowledgmentUnavailable(ValueError):
    pass


def ensure_sos_acknowledgment_indexes(db):
    db.safe_meet_sos_acknowledgments.create_index(
        [("sos_event_id", ASCENDING),
         ("contact_id", ASCENDING)],
        unique=True,
    )


def acknowledge_contact_sos(
    *, database, sos_event_id, contact_id, now
):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SOSAcknowledgmentUnavailable("Invalid timestamp.")

    moment = now.astimezone(timezone.utc)
    try:
        event_id = ObjectId(str(sos_event_id))
        contact = ObjectId(str(contact_id))
    except Exception:
        raise SOSAcknowledgmentUnavailable("SOS unavailable.")

    # Never accept acknowledgments for a stale or unauthorized SOS.
    item = database.safe_meet_sos_escalations.find_one({
        "sos_event_id": event_id,
        "contact_id": contact,
        "status": "pending_manual_review",
        "delivery_enabled": False,
    })
    if not item:
        raise SOSAcknowledgmentUnavailable("SOS unavailable.")

    def check_authorization():
        allowed = authorize_sos_escalation(
            database=database,
            escalation_id=item["_id"],
            now=moment,
        )
        if not allowed.get("allowed"):
            raise SOSAcknowledgmentUnavailable("SOS unavailable.")
        if allowed.get("contact_id") != contact:
            raise SOSAcknowledgmentUnavailable("SOS unavailable.")

    check_authorization()

    key = {"sos_event_id": event_id, "contact_id": contact}
    existing = database.safe_meet_sos_acknowledgments.find_one(key)
    if existing:
        return {
            "sos_event_id": str(event_id),
            "status": "acknowledged",
            "replayed": True,
            "notification_delivered": False,
        }

    meeting = item["safe_meet_id"]
    owner = item["owner_id"]
    revision = item["consent_revision"]

    try:
        with database.client.start_session() as session:
            with session.start_transaction():
                # These writes serialize the acknowledgment against
                # cancellation and consent/acceptance changes.
                fence = database.safe_meet_plans.update_one(
                    {
                        "_id": meeting,
                        "status": "confirmed",
                        "participant_ids": str(owner),
                    },
                    {"$inc": {"sos_ack_fence": 1}},
                    session=session,
                )
                if fence.matched_count != 1:
                    raise SOSAcknowledgmentUnavailable("SOS unavailable.")

                consent = database.safe_meet_contact_consents.update_one(
                    {
                        "safe_meet_id": meeting,
                        "owner_id": owner,
                        "contact_id": contact,
                        "revision": revision,
                        "status": "active",
                        "expires_at": {"$gt": moment},
                    },
                    {"$inc": {"sos_ack_fence": 1}},
                    session=session,
                )
                if consent.matched_count != 1:
                    raise SOSAcknowledgmentUnavailable("SOS unavailable.")

                acceptance = database.safe_meet_contact_acceptances.update_one(
                    {
                        "safe_meet_id": meeting,
                        "owner_id": owner,
                        "contact_id": contact,
                        "invitation_revision": revision,
                        "decision": "accepted",
                    },
                    {"$inc": {"sos_ack_fence": 1}},
                    session=session,
                )
                if acceptance.matched_count != 1:
                    raise SOSAcknowledgmentUnavailable("SOS unavailable.")

                if not database.users.find_one(
                    {"_id": contact, "account_status": "active"},
                    {"_id": 1},
                    session=session,
                ):
                    raise SOSAcknowledgmentUnavailable("SOS unavailable.")

                if not database.safe_meet_sos_escalations.find_one(
                    {
                        "_id": item["_id"],
                        "status": "pending_manual_review",
                        "delivery_enabled": False,
                    },
                    {"_id": 1},
                    session=session,
                ):
                    raise SOSAcknowledgmentUnavailable("SOS unavailable.")

                database.safe_meet_sos_acknowledgments.insert_one(
                    {
                        **key,
                        "safe_meet_id": meeting,
                        "owner_id": owner,
                        "consent_revision": revision,
                        "status": "acknowledged",
                        "acknowledged_at": moment,
                    },
                    session=session,
                )
    except DuplicateKeyError:
        check_authorization()
        if database.safe_meet_sos_acknowledgments.find_one(key):
            return {
                "sos_event_id": str(event_id),
                "status": "acknowledged",
                "replayed": True,
                "notification_delivered": False,
            }
        raise

    return {
        "sos_event_id": str(event_id),
        "status": "acknowledged",
        "replayed": False,
        "notification_delivered": False,
    }
