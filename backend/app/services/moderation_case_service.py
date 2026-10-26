"""Phase 2O moderation case service.

All mutations use atomic expected-state transitions.
Report contents remain private to authorized reviewers.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument

from app.extensions import mongo


STATES = {"submitted", "in_review", "resolved", "dismissed"}

TRANSITIONS = {
    "submitted": {"in_review", "resolved", "dismissed"},
    "in_review": {"resolved", "dismissed"},
    "resolved": set(),
    "dismissed": set(),
}


class CaseError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def object_id(value):
    if not isinstance(value, (str, ObjectId)):
        raise CaseError("INVALID_ID", "Invalid identifier")
    if not ObjectId.is_valid(str(value)):
        raise CaseError("INVALID_ID", "Invalid identifier")
    return ObjectId(str(value))


def serialize_case(item):
    return {
        "id": str(item["_id"]),
        "reporter_id": str(item["reporter_id"]),
        "reported_user_id": str(item["target_user_id"]),
        "reason": item["reason"],
        "description": item.get("description", ""),
        "status": item["status"],
        "severity": item.get("severity", 0),
        "created_at": item["created_at"].isoformat(),
        "updated_at": (
            item["updated_at"].isoformat()
            if item.get("updated_at") else None
        ),
    }


def list_cases(status="submitted", limit=25, after=None):
    if status not in STATES:
        raise CaseError("INVALID_STATUS", "Invalid case status")

    try:
        if isinstance(limit, bool):
            raise ValueError()
        size = int(limit)
    except (TypeError, ValueError):
        raise CaseError("INVALID_LIMIT", "Invalid page limit") from None

    if not 1 <= size <= 50:
        raise CaseError("INVALID_LIMIT", "Limit must be 1 to 50")

    query = {"status": status}
    if after:
        query["_id"] = {"$lt": object_id(after)}

    rows = list(
        mongo.db.reports.find(query)
        .sort("_id", -1)
        .limit(size + 1)
    )

    selected = rows[:size]
    return {
        "cases": [serialize_case(row) for row in selected],
        "next_cursor": (
            str(selected[-1]["_id"])
            if len(rows) > size and selected
            else None
        ),
    }


def decide_case(actor_id, report_id, payload):
    if not isinstance(payload, dict):
        raise CaseError("INVALID_BODY", "JSON object required")

    if set(payload) != {"expected_status", "decision", "reason"}:
        raise CaseError("INVALID_FIELDS", "Unexpected fields")

    expected = payload["expected_status"]
    decision = payload["decision"]
    reason = payload["reason"]

    if (
        not isinstance(expected, str)
        or not isinstance(decision, str)
        or decision not in TRANSITIONS.get(expected, set())
    ):
        raise CaseError("INVALID_TRANSITION", "Invalid case transition")

    if not isinstance(reason, str):
        raise CaseError("INVALID_REASON", "Review reason must be text")

    reason = reason.strip()
    if not 1 <= len(reason) <= 1000:
        raise CaseError("INVALID_REASON", "Review reason required")

    if any(ord(c) < 32 or ord(c) == 127 for c in reason):
        raise CaseError("INVALID_REASON", "Invalid review reason")

    actor = object_id(actor_id)
    report = object_id(report_id)
    now = datetime.now(timezone.utc)

    event = {
        "actor_id": actor,
        "from_status": expected,
        "to_status": decision,
        "reason": reason,
        "created_at": now,
    }

    row = mongo.db.reports.find_one_and_update(
        {"_id": report, "status": expected},
        {
            "$set": {
                "status": decision,
                "reviewed_by": actor,
                "reviewed_at": now,
                "updated_at": now,
            },
            "$push": {"review_history": event},
        },
        return_document=ReturnDocument.AFTER,
    )

    if row is None:
        raise CaseError(
            "CASE_CONFLICT",
            "Report missing or already changed",
            409,
        )

    return serialize_case(row)


def add_case_note(actor_id, report_id, note_id, content):
    """Append a private reviewer note atomically and idempotently."""
    from uuid import UUID
    from pymongo import ReturnDocument

    actor = object_id(actor_id)
    report = object_id(report_id)

    try:
        if not isinstance(note_id, str):
            raise ValueError()
        note_id = str(UUID(note_id))
    except (ValueError, TypeError, AttributeError):
        raise CaseError(
            "INVALID_NOTE_ID", "Valid UUID note ID required."
        ) from None

    if (
        not isinstance(content, str)
        or not 1 <= len(content.strip()) <= 2000
        or any(ord(c) < 32 and c not in "\\n\\t" for c in content)
    ):
        raise CaseError(
            "INVALID_NOTE", "Valid note text required."
        )

    text = content.strip()
    now = datetime.now(timezone.utc)
    entry = {
        "note_id": note_id,
        "actor_id": actor,
        "content": text,
        "created_at": now,
    }

    # Filter guarantees that retries cannot add a duplicate note.
    row = mongo.db.reports.find_one_and_update(
        {
            "_id": report,
            "admin_notes.note_id": {"$ne": note_id},
        },
        {
            "$push": {"admin_notes": entry},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )

    if row is None:
        existing = mongo.db.reports.find_one(
            {"_id": report},
            {"admin_notes": 1},
        )
        if existing is None:
            raise CaseError(
                "CASE_NOT_FOUND", "Report not found.", 404
            )
        for note in existing.get("admin_notes", []):
            if note.get("note_id") == note_id:
                if (
                    note.get("actor_id") != actor
                    or note.get("content") != text
                ):
                    raise CaseError(
                        "NOTE_CONFLICT",
                        "Note ID already belongs to different content.",
                        409,
                    )
                return {"note_id": note_id, "created": False}
        raise CaseError(
            "NOTE_CONFLICT", "Note could not be added.", 409
        )

    return {"note_id": note_id, "created": True}


def list_case_notes(actor_id, report_id, limit=50):
    """Private admin-only note listing; route authenticates reviewer."""
    object_id(actor_id)
    report = object_id(report_id)

    try:
        if isinstance(limit, bool):
            raise ValueError()
        limit = int(limit)
    except (ValueError, TypeError):
        raise CaseError("INVALID_LIMIT", "Invalid note limit") from None

    if not 1 <= limit <= 100:
        raise CaseError("INVALID_LIMIT", "Limit must be 1 to 100")

    record = mongo.db.reports.find_one(
        {"_id": report},
        {"admin_notes": 1},
    )

    if record is None:
        raise CaseError("CASE_NOT_FOUND", "Report not found", 404)

    notes = record.get("admin_notes", [])
    if not isinstance(notes, list):
        raise CaseError("INVALID_CASE", "Invalid note storage", 409)

    selected = notes[-limit:]
    output = []

    for note in selected:
        if (
            not isinstance(note, dict)
            or not isinstance(note.get("note_id"), str)
            or not isinstance(note.get("content"), str)
            or "actor_id" not in note
            or not hasattr(note.get("created_at"), "isoformat")
        ):
            raise CaseError("INVALID_CASE", "Invalid note storage", 409)

        output.append({
            "note_id": note["note_id"],
            "content": note["content"],
            "reviewer_id": str(note["actor_id"]),
            "created_at": note["created_at"].isoformat(),
        })

    return {"report_id": str(report), "notes": output}
