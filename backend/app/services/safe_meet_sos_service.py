
"""Safe Meet SOS event and disabled-delivery escalation foundation."""
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError
from app.services.safe_meet_trusted_contact_acceptance_service import (
    get_verified_trusted_contact,
)


class SafeMeetSOSError(ValueError):
    pass


def _oid(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise SafeMeetSOSError("Invalid identifier.")


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetSOSError("Timezone-aware timestamp required.")
    return value.astimezone(timezone.utc)


def ensure_safe_meet_sos_indexes(db):
    db.safe_meet_sos_events.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING),
         ("idempotency_key", ASCENDING)],
        unique=True,
    )
    db.safe_meet_sos_escalations.create_index(
        [("sos_event_id", ASCENDING)], unique=True,
    )


def trigger_safe_meet_sos(
    *, database, safe_meet_id, owner_id, idempotency_key, now,
):
    meeting, owner = _oid(safe_meet_id), _oid(owner_id)
    moment = _utc(now)
    if (
        type(idempotency_key) is not str
        or not 8 <= len(idempotency_key) <= 128
        or not all(
            char.isascii() and (
                char.isalnum() or char in "-_"
            )
            for char in idempotency_key
        )
    ):
        raise SafeMeetSOSError("Invalid idempotency key.")

    key = {
        "safe_meet_id": meeting,
        "owner_id": owner,
        "idempotency_key": idempotency_key,
    }

    def response(event):
        return {
            "sos_event_id": str(event["_id"]),
            "status": event["status"],
            "escalation_status": event["escalation_status"],
            "replayed": event["_id"] != new_event_id,
        }

    new_event_id = ObjectId()
    existing = database.safe_meet_sos_events.find_one(key)
    if existing:
        return response(existing)

    try:
        with database.client.start_session() as session:
            with session.start_transaction():
                plan = database.safe_meet_plans.find_one(
                    {"_id": meeting, "status": "confirmed"},
                    session=session,
                )
                if not plan or str(owner) not in plan.get("participant_ids", []):
                    raise SafeMeetSOSError("Safe Meet unavailable.")

                user = database.users.find_one(
                    {"_id": owner, "account_status": "active"},
                    {"_id": 1},
                    session=session,
                )
                if user is None:
                    raise SafeMeetSOSError("Participant unavailable.")

                verified = get_verified_trusted_contact(
                    database=database,
                    safe_meet_id=meeting,
                    owner_id=owner,
                    now=moment,
                )

                # Fence against concurrent cancellation.
                changed = database.safe_meet_plans.update_one(
                    {
                        "_id": meeting,
                        "status": "confirmed",
                        "participant_ids": str(owner),
                    },
                    {"$inc": {"sos_write_fence": 1}},
                    session=session,
                )
                if changed.matched_count != 1:
                    raise SafeMeetSOSError("Safe Meet changed.")

                # Fence the accepted consent against revocation.
                if verified is not None:
                    consent_guard = database.safe_meet_contact_consents.update_one(
                        {
                            "safe_meet_id": meeting,
                            "owner_id": owner,
                            "status": "active",
                            "revision": verified["invitation_revision"],
                            "contact_id": verified["contact_id"],
                            "expires_at": {"$gt": moment},
                        },
                        {"$inc": {"sos_write_fence": 1}},
                        session=session,
                    )
                    if consent_guard.matched_count != 1:
                        verified = None

                status = (
                    "pending_manual_review"
                    if verified else "no_verified_contact"
                )
                event = {
                    **key,
                    "_id": new_event_id,
                    "status": "recorded",
                    "escalation_status": status,
                    "created_at": moment,
                }
                database.safe_meet_sos_events.insert_one(
                    event, session=session
                )

                if verified is not None:
                    database.safe_meet_sos_escalations.insert_one(
                        {
                            "sos_event_id": new_event_id,
                            "safe_meet_id": meeting,
                            "owner_id": owner,
                            "contact_id": verified["contact_id"],
                            "consent_revision": verified["invitation_revision"],
                            "status": "pending_manual_review",
                            "delivery_enabled": False,
                            "created_at": moment,
                        },
                        session=session,
                    )
    except DuplicateKeyError:
        existing = database.safe_meet_sos_events.find_one(key)
        if existing is None:
            raise
        return response(existing)

    return response(event)
