
"""Atomic, auditable Superadmin account moderation."""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo


STATES = frozenset({"active", "suspended", "banned"})
TRANSITIONS = {
    "active": frozenset({"suspended", "banned"}),
    "suspended": frozenset({"active", "banned"}),
    "banned": frozenset({"active"}),
}


class AccountModerationError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def as_oid(value):
    if not isinstance(value, (str, ObjectId)):
        raise AccountModerationError("INVALID_ID", "Invalid ID")
    if not ObjectId.is_valid(str(value)):
        raise AccountModerationError("INVALID_ID", "Invalid ID")
    return ObjectId(str(value))


def validate_action(expected_status, decision, reason, report_id=None):
    if (
        not isinstance(expected_status, str)
        or not isinstance(decision, str)
        or decision not in TRANSITIONS.get(expected_status, frozenset())
    ):
        raise AccountModerationError(
            "INVALID_TRANSITION", "Unsupported account transition"
        )

    if not isinstance(reason, str):
        raise AccountModerationError(
            "INVALID_REASON", "Reason must be text"
        )
    reason = reason.strip()

    if not 10 <= len(reason) <= 1000:
        raise AccountModerationError(
            "INVALID_REASON", "Reason must contain 10-1000 characters"
        )

    if any(ord(c) < 32 or ord(c) == 127 for c in reason):
        raise AccountModerationError(
            "INVALID_REASON", "Invalid control characters"
        )

    linked_report = as_oid(report_id) if report_id is not None else None
    return expected_status, decision, reason, linked_report


def moderate_account(
    admin_id, user_id, expected_status, decision, reason,
    report_id=None,
):
    actor = as_oid(admin_id)
    target = as_oid(user_id)

    expected, decision, reason, linked_report = validate_action(
        expected_status, decision, reason, report_id
    )

    # A report may only be linked when it concerns the target user.
    if linked_report is not None:
        report = mongo.db.reports.find_one({
            "_id": linked_report,
            "target_user_id": target,
        }, {"_id": 1})
        if report is None:
            raise AccountModerationError(
                "REPORT_NOT_FOUND",
                "Report does not concern this user",
                404,
            )

    now = datetime.now(timezone.utc)
    event = {
        "moderator_id": actor,
        "from_status": expected,
        "to_status": decision,
        "reason": reason,
        "report_id": linked_report,
        "created_at": now,
    }

    # State transition and its audit event are one MongoDB write.
    updated = mongo.db.users.find_one_and_update(
        {"_id": target, "account_status": expected},
        {
            "$set": {
                "account_status": decision,
                "updated_at": now,
            },
            "$push": {
                "account_moderation_history": event,
            },
        },
        return_document=ReturnDocument.AFTER,
        projection={
            "_id": 1,
            "account_status": 1,
        },
    )

    if updated is None:
        raise AccountModerationError(
            "ACCOUNT_CONFLICT",
            "User unavailable or status changed",
            409,
        )

    return {
        "user_id": str(updated["_id"]),
        "account_status": updated["account_status"],
    }
