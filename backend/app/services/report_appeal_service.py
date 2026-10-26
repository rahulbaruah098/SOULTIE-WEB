"""Phase 2O appeal creation and administrative review.

Mongo reports remain authoritative; appeal records are private.
"""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.extensions import mongo


class AppealError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def as_oid(value):
    if not isinstance(value, (str, ObjectId)):
        raise AppealError("INVALID_ID", "Invalid identifier")
    if not ObjectId.is_valid(str(value)):
        raise AppealError("INVALID_ID", "Invalid identifier")
    return ObjectId(str(value))


def appeal_reason(value):
    if not isinstance(value, str):
        raise AppealError("INVALID_REASON", "Reason must be text")
    value = value.strip()
    if not 10 <= len(value) <= 2000:
        raise AppealError(
            "INVALID_REASON",
            "Reason must contain 10 to 2000 characters",
        )
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise AppealError("INVALID_REASON", "Invalid characters")
    return value


def public_appeal(item):
    return {
        "id": str(item["_id"]),
        "report_id": str(item["report_id"]),
        "status": item["status"],
        "reason": item["reason"],
        "created_at": item["created_at"].isoformat(),
        "resolved_at": (
            item["resolved_at"].isoformat()
            if item.get("resolved_at") else None
        ),
    }


def submit_appeal(user_id, report_id, reason):
    actor = as_oid(user_id)
    report = as_oid(report_id)
    explanation = appeal_reason(reason)

    # Only the original reporter may appeal a dismissed report.
    original = mongo.db.reports.find_one({
        "_id": report,
        "reporter_id": actor,
        "status": "dismissed",
    })

    if original is None:
        raise AppealError(
            "REPORT_NOT_APPEALABLE",
            "Report not found or not eligible for appeal",
            404,
        )

    now = datetime.now(timezone.utc)
    lookup = {"report_id": report, "appellant_id": actor}
    document = {
        **lookup,
        "reason": explanation,
        "status": "submitted",
        "created_at": now,
        "updated_at": now,
        "history": [],
    }

    # A unique index on report_id + appellant_id must be
    # installed before this endpoint is enabled for traffic.
    try:
        row = mongo.db.report_appeals.find_one_and_update(
            lookup,
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        row = mongo.db.report_appeals.find_one(lookup)
        if row is None:
            raise

    return {
        "appeal": public_appeal(row),
        "created": row["created_at"] == now,
    }


def list_appeals(status="submitted", limit=25):
    if status not in {"submitted", "accepted", "rejected"}:
        raise AppealError("INVALID_STATUS", "Invalid status")
    try:
        if isinstance(limit, bool):
            raise ValueError()
        size = int(limit)
    except (ValueError, TypeError):
        raise AppealError("INVALID_LIMIT", "Invalid limit") from None

    if not 1 <= size <= 50:
        raise AppealError("INVALID_LIMIT", "Limit must be 1 to 50")

    rows = mongo.db.report_appeals.find(
        {"status": status}
    ).sort("_id", -1).limit(size)

    return {"appeals": [public_appeal(row) for row in rows]}


def review_appeal(admin_id, appeal_id, decision, reason):
    """Review an appeal with atomic accepted-appeal reopening."""
    actor = as_oid(admin_id)
    identifier = as_oid(appeal_id)

    if decision not in {"accepted", "rejected"}:
        raise AppealError("INVALID_DECISION", "Invalid decision")

    explanation = appeal_reason(reason)
    now = datetime.now(timezone.utc)

    event = {
        "actor_id": actor,
        "action": decision,
        "reason": explanation,
        "created_at": now,
    }
    selector = {"_id": identifier, "status": "submitted"}
    modification = {
        "$set": {
            "status": decision,
            "resolved_by": actor,
            "resolved_at": now,
            "updated_at": now,
        },
        "$push": {"history": event},
    }

    # Rejection changes one appeal document and is atomic.
    if decision == "rejected":
        row = mongo.db.report_appeals.find_one_and_update(
            selector,
            modification,
            return_document=ReturnDocument.AFTER,
        )
        if row is None:
            raise AppealError(
                "APPEAL_CONFLICT",
                "Appeal not found or already reviewed",
                409,
            )
        return public_appeal(row)

    # Acceptance modifies both an appeal and its report.
    # Both writes must commit or neither may persist.
    # Transactions require a transaction-capable MongoDB
    # deployment, normally a replica set.
    with mongo.cx.start_session() as session:
        with session.start_transaction():
            appeal = mongo.db.report_appeals.find_one(
                selector, session=session
            )
            if appeal is None:
                raise AppealError(
                    "APPEAL_CONFLICT",
                    "Appeal not found or already reviewed",
                    409,
                )

            report_result = mongo.db.reports.update_one(
                {
                    "_id": appeal["report_id"],
                    "reporter_id": appeal["appellant_id"],
                    "status": "dismissed",
                },
                {
                    "$set": {
                        "status": "in_review",
                        "updated_at": now,
                    },
                    "$push": {
                        "review_history": {
                            "actor_id": actor,
                            "from_status": "dismissed",
                            "to_status": "in_review",
                            "reason": "Appeal accepted: " + explanation,
                            "created_at": now,
                        },
                    },
                },
                session=session,
            )

            if report_result.matched_count != 1:
                raise AppealError(
                    "REPORT_CONFLICT",
                    "Original report is no longer appealable",
                    409,
                )

            updated = mongo.db.report_appeals.find_one_and_update(
                selector,
                modification,
                return_document=ReturnDocument.AFTER,
                session=session,
            )

            if updated is None:
                raise AppealError(
                    "APPEAL_CONFLICT",
                    "Appeal was already reviewed",
                    409,
                )

    return public_appeal(updated)
