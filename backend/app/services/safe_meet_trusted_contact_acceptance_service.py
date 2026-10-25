
"""Trusted-contact invitation acceptance; no location disclosure."""
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ASCENDING


class TrustedContactAcceptanceError(ValueError):
    pass


def _id(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise TrustedContactAcceptanceError("Invalid identifier.")


def _time(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TrustedContactAcceptanceError("Aware UTC timestamp required.")
    return value.astimezone(timezone.utc)


def ensure_trusted_contact_acceptance_indexes(database):
    database.safe_meet_contact_acceptances.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING)],
        unique=True,
    )
    database.safe_meet_contact_acceptance_events.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING),
         ("sequence", ASCENDING)],
        unique=True,
    )


def respond_to_trusted_contact_invitation(
    *, database, safe_meet_id, owner_id, contact_id, decision, now
):
    if decision not in ("accept", "reject"):
        raise TrustedContactAcceptanceError("Invalid decision.")
    meeting, owner, contact = map(
        _id, (safe_meet_id, owner_id, contact_id)
    )
    moment = _time(now)
    key = {"safe_meet_id": meeting, "owner_id": owner}

    with database.client.start_session() as session:
        with session.start_transaction():
            plan = database.safe_meet_plans.find_one(
                {"_id": meeting, "status": "confirmed"}, session=session
            )
            if not plan or str(owner) not in plan.get("participant_ids", []):
                raise TrustedContactAcceptanceError("Meeting unavailable.")

            invitation = database.safe_meet_contact_consents.find_one(
                {**key, "status": "active", "contact_id": contact},
                session=session,
            )
            if invitation is None:
                raise TrustedContactAcceptanceError("Invitation unavailable.")

            expiry = invitation.get("expires_at")
            # PyMongo's default timezone-naive BSON values represent UTC.
            if not isinstance(expiry, datetime):
                raise TrustedContactAcceptanceError("Invalid expiry.")
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= moment:
                raise TrustedContactAcceptanceError("Invitation expired.")

            contact_user = database.users.find_one(
                {"_id": contact, "account_status": "active"},
                {"_id": 1},
                session=session,
            )
            if not contact_user:
                raise TrustedContactAcceptanceError("Contact unavailable.")

            existing = database.safe_meet_contact_acceptances.find_one(
                key, session=session
            )
            revision = invitation["revision"]
            if existing and existing.get("invitation_revision") == revision:
                raise TrustedContactAcceptanceError(
                    "Invitation already answered."
                )

            sequence = (existing or {}).get("sequence", 0) + 1
            record = {
                **key,
                "contact_id": contact,
                "invitation_revision": revision,
                "decision": "accepted" if decision == "accept" else "rejected",
                "responded_at": moment,
                "sequence": sequence,
            }

            # CAS prevents competing accept/reject decisions.
            if existing:
                result = database.safe_meet_contact_acceptances.replace_one(
                    {**key, "sequence": existing["sequence"]},
                    record,
                    session=session,
                )
                if result.matched_count != 1:
                    raise TrustedContactAcceptanceError(
                        "Concurrent invitation response."
                    )
            else:
                database.safe_meet_contact_acceptances.insert_one(
                    record, session=session
                )

            # Fence the current consent record: revocation or
            # replacement must not race silently with acceptance.
            result = database.safe_meet_contact_consents.update_one(
                {
                    **key,
                    "revision": revision,
                    "status": "active",
                    "contact_id": contact,
                },
                {"$inc": {"acceptance_write_fence": 1}},
                session=session,
            )
            if result.matched_count != 1:
                raise TrustedContactAcceptanceError(
                    "Invitation changed concurrently."
                )

            database.safe_meet_contact_acceptance_events.insert_one(
                {
                    **key,
                    "sequence": sequence,
                    "contact_id": contact,
                    "invitation_revision": revision,
                    "decision": record["decision"],
                    "at": moment,
                },
                session=session,
            )
    return {"decision": record["decision"], "revision": revision}


def get_verified_trusted_contact(
    *, database, safe_meet_id, owner_id, now
):
    meeting, owner = map(_id, (safe_meet_id, owner_id))
    moment = _time(now)
    key = {"safe_meet_id": meeting, "owner_id": owner}

    consent = database.safe_meet_contact_consents.find_one(
        {**key, "status": "active"}
    )
    if not consent:
        return None

    expiry = consent.get("expires_at")
    if not isinstance(expiry, datetime):
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= moment:
        return None

    plan = database.safe_meet_plans.find_one(
        {"_id": meeting, "status": "confirmed"},
        {"participant_ids": 1},
    )
    if not plan or str(owner) not in plan.get("participant_ids", []):
        return None

    acceptance = database.safe_meet_contact_acceptances.find_one({
        **key,
        "contact_id": consent["contact_id"],
        "invitation_revision": consent["revision"],
        "decision": "accepted",
    })
    if not acceptance:
        return None

    if not database.users.find_one({
        "_id": consent["contact_id"], "account_status": "active"
    }, {"_id": 1}):
        return None

    # This result authorizes relationship status only.
    # It does not authorize location disclosure or sending alerts.
    return {
        "contact_id": consent["contact_id"],
        "location_enabled": consent["location_enabled"] is True,
        "expires_at": expiry,
        "invitation_revision": consent["revision"],
    }
