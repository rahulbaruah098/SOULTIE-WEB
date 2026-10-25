
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pymongo import ASCENDING, ReturnDocument

MAX_ATTEMPTS = 3

def ensure_sos_simulation_indexes(db):
    db.safe_meet_sos_escalations.create_index([
        ("status", ASCENDING),
        ("next_attempt_at", ASCENDING),
        ("lease_until", ASCENDING),
    ])

def _authorized(db, item, now):
    meeting = item["safe_meet_id"]
    owner = item["owner_id"]
    contact = item["contact_id"]
    revision = item["consent_revision"]

    return all((
        db.safe_meet_sos_events.find_one({
            "_id": item["sos_event_id"],
            "safe_meet_id": meeting,
            "owner_id": owner,
            "status": "recorded",
        }, {"_id": 1}),
        db.safe_meet_plans.find_one({
            "_id": meeting,
            "status": "confirmed",
            "participant_ids": str(owner),
        }, {"_id": 1}),
        db.safe_meet_contact_consents.find_one({
            "safe_meet_id": meeting,
            "owner_id": owner,
            "contact_id": contact,
            "revision": revision,
            "status": "active",
            "expires_at": {"$gt": now},
        }, {"_id": 1}),
        db.safe_meet_contact_acceptances.find_one({
            "safe_meet_id": meeting,
            "owner_id": owner,
            "contact_id": contact,
            "invitation_revision": revision,
            "decision": "accepted",
        }, {"_id": 1}),
        db.users.find_one({
            "_id": contact,
            "account_status": "active",
        }, {"_id": 1}),
    ))

def process_one_sos_simulation(*, database, simulate, now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Aware timestamp required.")
    if not callable(simulate):
        raise ValueError("Simulation callback required.")

    moment = now.astimezone(timezone.utc)
    lease = uuid4().hex

    item = database.safe_meet_sos_escalations.find_one_and_update(
        {
            "delivery_enabled": False,
            "$or": [
                {
                    "status": {"$in": [
                        "pending_manual_review", "simulation_retry"
                    ]},
                    "$or": [
                        {"next_attempt_at": {"$exists": False}},
                        {"next_attempt_at": {"$lte": moment}},
                    ],
                },
                {
                    "status": "simulation_processing",
                    "lease_until": {"$lte": moment},
                },
            ],
        },
        {"$set": {
            "status": "simulation_processing",
            "lease_id": lease,
            "lease_until": moment + timedelta(seconds=30),
        }},
        sort=[("created_at", ASCENDING), ("_id", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )

    if item is None:
        return {"processed": False}

    key = {
        "_id": item["_id"],
        "lease_id": lease,
        "status": "simulation_processing",
    }

    def finish(status, extra=None):
        result = database.safe_meet_sos_escalations.update_one(
            key,
            {
                "$set": {
                    "status": status,
                    "processed_at": moment,
                    **(extra or {}),
                },
                "$unset": {"lease_id": "", "lease_until": ""},
            },
        )
        if result.matched_count != 1:
            raise RuntimeError("Simulation lease lost.")

    if not _authorized(database, item, moment):
        finish("suppressed", {
            "suppression_reason": "authorization_unavailable"
        })
        return {
            "processed": True, "status": "suppressed",
            "delivered": False,
        }

    attempt = int(item.get("simulation_attempts", 0)) + 1
    try:
        if simulate(str(item["sos_event_id"])) is not True:
            raise RuntimeError("Simulated failure.")
    except Exception:
        # SOS_INFLIGHT_REVALIDATION_CP16G
        # Never schedule a retry after consent becomes invalid.
        if not _authorized(database, item, moment):
            finish("suppressed", {
                "suppression_reason": "authorization_revoked_inflight",
                "simulation_attempts": attempt,
            })
            return {
                "processed": True, "status": "suppressed",
                "delivered": False,
            }
        if attempt >= MAX_ATTEMPTS:
            finish("simulation_failed", {
                "simulation_attempts": attempt,
            })
            return {
                "processed": True,
                "status": "simulation_failed",
                "delivered": False,
            }

        finish("simulation_retry", {
            "simulation_attempts": attempt,
            "next_attempt_at": moment + timedelta(
                seconds=30 * (2 ** (attempt - 1))
            ),
        })
        return {
            "processed": True,
            "status": "simulation_retry",
            "delivered": False,
        }

    # A callback's successful return does not override revocation.
    if not _authorized(database, item, moment):
        finish("suppressed", {
            "suppression_reason": "authorization_revoked_inflight",
            "simulation_attempts": attempt,
        })
        return {
            "processed": True, "status": "suppressed",
            "delivered": False,
        }

    finish("simulation_succeeded", {
        "simulation_attempts": attempt,
    })
    return {
        "processed": True,
        "status": "simulation_succeeded",
        "delivered": False,
    }
