
"""Explicit, time-limited Safe Meet trusted-contact consent.

Foundation only: does not expose location or dispatch alerts.
"""
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError


class SafeMeetConsentError(ValueError):
    pass


def _oid(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise SafeMeetConsentError("Invalid identifier.")


def _moment(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetConsentError("Timezone-aware time required.")
    return value.astimezone(timezone.utc)


def ensure_trusted_contact_consent_indexes(database):
    database.safe_meet_contact_consents.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING)],
        unique=True,
    )
    database.safe_meet_contact_consent_events.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING),
         ("sequence", ASCENDING)],
        unique=True,
    )


def grant_trusted_contact_consent(
    *, database, safe_meet_id, owner_id, contact_id,
    expires_at, location_enabled=False, now,
):
    meeting = _oid(safe_meet_id)
    owner = _oid(owner_id)
    contact = _oid(contact_id)
    moment = _moment(now)
    expiry = _moment(expires_at)

    if owner == contact:
        raise SafeMeetConsentError("Cannot select yourself as contact.")
    if type(location_enabled) is not bool:
        raise SafeMeetConsentError("Location consent must be explicit.")
    if not moment < expiry <= moment + timedelta(hours=24):
        raise SafeMeetConsentError("Consent duration must be within 24 hours.")

    with database.client.start_session() as session:
        with session.start_transaction():
            plan = database.safe_meet_plans.find_one(
                {"_id": meeting}, session=session
            )
            if not plan or plan.get("status") != "confirmed":
                raise SafeMeetConsentError("Confirmed Safe Meet required.")
            if str(owner) not in plan.get("participant_ids", []):
                raise SafeMeetConsentError("Owner is not a participant.")

            contact_user = database.users.find_one(
                {"_id": contact, "account_status": "active"},
                {"_id": 1},
                session=session,
            )
            if contact_user is None:
                raise SafeMeetConsentError("Trusted contact is unavailable.")

            key = {"safe_meet_id": meeting, "owner_id": owner}
            previous = database.safe_meet_contact_consents.find_one(
                key, session=session
            )
            if previous and previous.get("status") == "active":
                raise SafeMeetConsentError(
                    "Active consent must be revoked before replacement."
                )

            revision = (previous or {}).get("revision", 0) + 1
            record = {
                **key,
                "contact_id": contact,
                "status": "active",
                "location_enabled": location_enabled,
                "expires_at": expiry,
                "updated_at": moment,
                "revision": revision,
            }
            if previous:
                result = database.safe_meet_contact_consents.replace_one(
                    {**key, "revision": previous["revision"]},
                    record,
                    session=session,
                )
                if result.matched_count != 1:
                    raise SafeMeetConsentError("Concurrent consent change.")
            else:
                database.safe_meet_contact_consents.insert_one(
                    record, session=session
                )

            database.safe_meet_contact_consent_events.insert_one(
                {
                    **key, "sequence": revision,
                    "action": "granted",
                    "contact_id": contact,
                    "location_enabled": location_enabled,
                    "at": moment,
                },
                session=session,
            )
    return {"status": "active", "revision": revision}


def revoke_trusted_contact_consent(
    *, database, safe_meet_id, owner_id, now,
):
    key = {
        "safe_meet_id": _oid(safe_meet_id),
        "owner_id": _oid(owner_id),
    }
    moment = _moment(now)

    with database.client.start_session() as session:
        with session.start_transaction():
            previous = database.safe_meet_contact_consents.find_one(
                key, session=session
            )
            if not previous or previous.get("status") != "active":
                raise SafeMeetConsentError("No active consent to revoke.")

            revision = previous["revision"] + 1
            result = database.safe_meet_contact_consents.update_one(
                {**key, "revision": previous["revision"], "status": "active"},
                {"$set": {
                    "status": "revoked",
                    "location_enabled": False,
                    "updated_at": moment,
                    "revision": revision,
                }},
                session=session,
            )
            if result.matched_count != 1:
                raise SafeMeetConsentError("Concurrent consent change.")

            database.safe_meet_contact_consent_events.insert_one(
                {
                    **key, "sequence": revision,
                    "action": "revoked",
                    "at": moment,
                },
                session=session,
            )
    return {"status": "revoked", "revision": revision}


def read_active_trusted_contact_consent(
    *, database, safe_meet_id, owner_id, now,
):
    moment = _moment(now)
    key = {
        "safe_meet_id": _oid(safe_meet_id),
        "owner_id": _oid(owner_id),
        "status": "active",
        "expires_at": {"$gt": moment},
    }
    record = database.safe_meet_contact_consents.find_one(key)
    if record is None:
        return None

    # Revoked/canceled meetings must not retain effective consent.
    plan = database.safe_meet_plans.find_one(
        {"_id": key["safe_meet_id"], "status": "confirmed"},
        {"_id": 1, "participant_ids": 1},
    )
    if not plan or str(key["owner_id"]) not in plan.get("participant_ids", []):
        return None
    return record
