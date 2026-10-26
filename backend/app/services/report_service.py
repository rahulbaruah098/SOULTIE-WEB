"""Private, durable Phase 2O user reporting."""

from datetime import datetime, timezone
from uuid import UUID
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.repositories.user_repository import get_user_by_id


REASONS = {
    "harassment", "threats", "hate", "spam",
    "impersonation", "fraud", "inappropriate_content",
    "underage", "safety_concern", "other",
}


class ReportError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _oid(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (ValueError, TypeError):
        raise ReportError("INVALID_USER_ID", "Invalid user ID") from None


def validate_report(reporter_id, payload):
    reporter = _oid(reporter_id)

    if not isinstance(payload, dict):
        raise ReportError("INVALID_REPORT", "JSON object required")

    allowed = {
        "reported_user_id", "reason", "description",
        "idempotency_key", "evidence_refs",
    }
    if set(payload) - allowed:
        raise ReportError("INVALID_REPORT", "Unsupported report fields")

    target = _oid(payload.get("reported_user_id"))
    if reporter == target:
        raise ReportError("SELF_REPORT", "Cannot report yourself")

    reason = payload.get("reason")
    if not isinstance(reason, str) or reason not in REASONS:
        raise ReportError("INVALID_REASON", "Invalid report reason")

    description = payload.get("description", "")
    if not isinstance(description, str) or len(description) > 2000:
        raise ReportError("INVALID_DESCRIPTION", "Invalid description")
    description = description.strip()
    if any(ord(c) < 32 and c not in "\n\t" for c in description):
        raise ReportError("INVALID_DESCRIPTION", "Invalid description")

    key = payload.get("idempotency_key")
    try:
        if not isinstance(key, str):
            raise ValueError()
        key = str(UUID(key))
    except (ValueError, TypeError, AttributeError):
        raise ReportError("INVALID_IDEMPOTENCY_KEY", "UUID required") from None

    refs = payload.get("evidence_refs", [])
    if not isinstance(refs, list) or len(refs) > 5:
        raise ReportError("INVALID_EVIDENCE", "Invalid evidence")
    for ref in refs:
        if not isinstance(ref, str) or not 1 <= len(ref) <= 128:
            raise ReportError("INVALID_EVIDENCE", "Invalid evidence reference")
        if not all(c.isascii() and (c.isalnum() or c in "-_:") for c in ref):
            raise ReportError("INVALID_EVIDENCE", "Invalid evidence reference")

    return reporter, target, reason, description, key, refs


def serialize_report(item):
    return {
        "id": str(item["_id"]),
        "reported_user_id": str(item["target_user_id"]),
        "reason": item["reason"],
        "description": item.get("description", ""),
        "status": item["status"],
        "created_at": item["created_at"].isoformat(),
    }



def validate_evidence_ownership(reporter, target, refs):
    """Require chat evidence to belong to both report participants.

    No raw media URLs, filesystem paths or arbitrary external references
    are accepted here. Other evidence sources need their own secure
    upload/ownership flow.
    """
    if not refs:
        return

    from app.repositories.chat_repository import (
        get_message_by_id,
        get_conversation_by_id,
    )

    for ref in refs:
        kind, sep, identifier = ref.partition(":")
        if (
            not sep
            or kind != "message"
            or not ObjectId.is_valid(identifier)
        ):
            raise ReportError(
                "UNSUPPORTED_EVIDENCE",
                "Only valid chat message evidence references are supported.",
            )

        message = get_message_by_id(ObjectId(identifier))
        if not isinstance(message, dict):
            raise ReportError(
                "EVIDENCE_NOT_AVAILABLE",
                "Evidence is not available for this report.",
                404,
            )

        conversation = get_conversation_by_id(
            message.get("conversation_id")
        )
        if not isinstance(conversation, dict):
            raise ReportError(
                "EVIDENCE_NOT_AVAILABLE",
                "Evidence is not available for this report.",
                404,
            )

        members = conversation.get("member_ids")
        if not isinstance(members, (list, tuple)) or len(members) != 2:
            raise ReportError(
                "EVIDENCE_NOT_AVAILABLE",
                "Evidence is not available for this report.",
                404,
            )

        try:
            member_set = {
                ObjectId(str(member))
                for member in members
            }
        except (ValueError, TypeError):
            raise ReportError(
                "EVIDENCE_NOT_AVAILABLE",
                "Evidence is not available for this report.",
                404,
            ) from None

        if member_set != {reporter, target}:
            raise ReportError(
                "EVIDENCE_NOT_AVAILABLE",
                "Evidence is not available for this report.",
                404,
            )

def create_report(reporter_id, payload):
    reporter, target, reason, description, key, refs = (
        validate_report(reporter_id, payload)
    )

    user = get_user_by_id(reporter)
    if not user or user.get("account_status") != "active":
        raise ReportError("ACCOUNT_UNAVAILABLE", "Account unavailable", 403)

    collection = mongo.db.reports
    lookup = {"reporter_id": reporter, "idempotency_key": key}

    existing = collection.find_one(lookup)
    if existing is not None:
        return {"report": serialize_report(existing), "created": False}

    if not get_user_by_id(target):
        raise ReportError("TARGET_NOT_FOUND", "User not found", 404)

    validate_evidence_ownership(reporter, target, refs)

    now = datetime.now(timezone.utc)
    document = {
        **lookup,
        "target_user_id": target,
        "reason": reason,
        "description": description,
        "evidence_refs": refs,
        "status": "submitted",
        "severity": 0,
        "created_at": now,
        "updated_at": now,
    }

    try:
        item = collection.find_one_and_update(
            lookup,
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        item = collection.find_one(lookup)
        if item is None:
            raise

    return {
        "report": serialize_report(item),
        "created": item["created_at"] == now,
    }


def list_my_reports(reporter_id, limit=20):
    reporter = _oid(reporter_id)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ReportError("INVALID_LIMIT", "Invalid limit") from None
    if not 1 <= limit <= 50:
        raise ReportError("INVALID_LIMIT", "Limit must be 1 to 50")

    items = mongo.db.reports.find(
        {"reporter_id": reporter}
    ).sort("created_at", -1).limit(limit)

    return {"reports": [serialize_report(item) for item in items]}
