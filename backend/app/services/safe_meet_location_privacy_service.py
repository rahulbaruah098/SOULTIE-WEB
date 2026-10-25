
"""Consent-gated Safe Meet location snapshots. No outbound transmission."""
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from pymongo import ASCENDING

from app.services.safe_meet_trusted_contact_acceptance_service import (
    get_verified_trusted_contact,
)


class SafeMeetLocationError(ValueError):
    pass


def _id(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise SafeMeetLocationError("Invalid identifier.")


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetLocationError("Timezone-aware timestamp required.")
    return value.astimezone(timezone.utc)


def ensure_safe_meet_location_indexes(database):
    database.safe_meet_location_snapshots.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING)],
        unique=True,
    )
    # Expired coordinates are automatically cleaned up by MongoDB.
    # Authorization checks still enforce expiry immediately.
    database.safe_meet_location_snapshots.create_index(
        "expires_at", expireAfterSeconds=0,
    )
    database.safe_meet_location_events.create_index(
        [("safe_meet_id", ASCENDING), ("owner_id", ASCENDING),
         ("sequence", ASCENDING)],
        unique=True,
    )


def _authorized(database, meeting, owner, now):
    verified = get_verified_trusted_contact(
        database=database,
        safe_meet_id=meeting,
        owner_id=owner,
        now=now,
    )
    if not verified or verified["location_enabled"] is not True:
        raise SafeMeetLocationError("Location sharing unavailable.")
    return verified


def publish_location_snapshot(
    *, database, safe_meet_id, owner_id, latitude, longitude, now,
):
    meeting, owner = _id(safe_meet_id), _id(owner_id)
    moment = _utc(now)

    if (type(latitude) not in (int, float)
        or type(longitude) not in (int, float)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180):
        raise SafeMeetLocationError("Invalid coordinates.")

    import math
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise SafeMeetLocationError("Invalid coordinates.")

    verified = _authorized(database, meeting, owner, moment)
    key = {"safe_meet_id": meeting, "owner_id": owner}

    with database.client.start_session() as session:
        with session.start_transaction():
            consent = database.safe_meet_contact_consents.find_one(
                {
                    **key,
                    "status": "active",
                    "revision": verified["invitation_revision"],
                    "location_enabled": True,
                    "contact_id": verified["contact_id"],
                    "expires_at": {"$gt": moment},
                },
                session=session,
            )
            if consent is None:
                raise SafeMeetLocationError("Location consent changed.")

            # Write fence serializes this operation against
            # a simultaneous revocation or consent replacement.
            fenced = database.safe_meet_contact_consents.update_one(
                {
                    **key,
                    "status": "active",
                    "revision": consent["revision"],
                    "location_enabled": True,
                },
                {"$inc": {"location_write_fence": 1}},
                session=session,
            )
            if fenced.matched_count != 1:
                raise SafeMeetLocationError("Location consent changed.")

            # SAFE_MEET_LOCATION_TRANSACTION_FENCES_CP15H
            # Serialize a location update against meeting cancellation.
            meeting_guard = database.safe_meet_plans.update_one(
                {
                    "_id": meeting,
                    "status": "confirmed",
                    "participant_ids": str(owner),
                },
                {"$inc": {"location_write_fence": 1}},
                session=session,
            )
            if meeting_guard.matched_count != 1:
                raise SafeMeetLocationError("Meeting unavailable.")

            # Serialize against contact acceptance replacement.
            acceptance_guard = database.safe_meet_contact_acceptances.update_one(
                {
                    **key,
                    "contact_id": verified["contact_id"],
                    "invitation_revision": verified["invitation_revision"],
                    "decision": "accepted",
                },
                {"$inc": {"location_write_fence": 1}},
                session=session,
            )
            if acceptance_guard.matched_count != 1:
                raise SafeMeetLocationError("Contact acceptance changed.")

            previous = database.safe_meet_location_snapshots.find_one(
                key, session=session
            )
            sequence = (previous or {}).get("sequence", 0) + 1
            expiry = min(
                moment + timedelta(minutes=5),
                verified["expires_at"],
            )
            snapshot = {
                **key,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "captured_at": moment,
                "expires_at": expiry,
                "invitation_revision": verified["invitation_revision"],
                "sequence": sequence,
            }
            database.safe_meet_location_snapshots.replace_one(
                key, snapshot, upsert=True, session=session
            )
            database.safe_meet_location_events.insert_one(
                {
                    **key,
                    "sequence": sequence,
                    "event": "snapshot_updated",
                    "at": moment,
                },
                session=session,
            )

    return {"status": "updated", "expires_at": expiry}


def read_location_for_contact(
    *, database, safe_meet_id, owner_id, requester_id, now,
):
    meeting = _id(safe_meet_id)
    owner = _id(owner_id)
    requester = _id(requester_id)
    moment = _utc(now)

    verified = _authorized(database, meeting, owner, moment)
    if requester != verified["contact_id"]:
        raise SafeMeetLocationError("Location unavailable.")

    snapshot = database.safe_meet_location_snapshots.find_one({
        "safe_meet_id": meeting,
        "owner_id": owner,
        "invitation_revision": verified["invitation_revision"],
        "expires_at": {"$gt": moment},
    })
    if snapshot is None:
        return None

    # SAFE_MEET_LOCATION_DISCLOSURE_RECHECK_CP15I
    # Recheck latest consent, contact decision, meeting status
    # and expiration after loading the coordinate document.
    current = _authorized(database, meeting, owner, moment)
    if (
        requester != current["contact_id"]
        or current["invitation_revision"]
        != snapshot["invitation_revision"]
    ):
        raise SafeMeetLocationError("Location unavailable.")

    return {
        "latitude": snapshot["latitude"],
        "longitude": snapshot["longitude"],
        "captured_at": snapshot["captured_at"],
        "expires_at": snapshot["expires_at"],
    }
