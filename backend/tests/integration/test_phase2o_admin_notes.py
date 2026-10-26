
"""Phase 2O administrator notes: privacy and idempotency."""

from uuid import uuid4
from bson import ObjectId
import pytest

from app.services import moderation_case_service as svc


def test_invalid_note_id_is_rejected():
    with pytest.raises(svc.CaseError) as exc:
        svc.add_case_note(
            ObjectId(), ObjectId(), "invalid", "Reviewer note"
        )
    assert exc.value.code == "INVALID_NOTE_ID"


def test_empty_note_rejected():
    with pytest.raises(svc.CaseError) as exc:
        svc.add_case_note(
            ObjectId(), ObjectId(), str(uuid4()), " "
        )
    assert exc.value.code == "INVALID_NOTE"


def test_notes_are_atomic_and_private(monkeypatch):
    actor, report = ObjectId(), ObjectId()
    note_id = str(uuid4())

    class Reports:
        def find_one_and_update(self, selector, update, **kwargs):
            assert selector["_id"] == report
            assert selector["admin_notes.note_id"] == {
                "$ne": note_id
            }
            note = update["$push"]["admin_notes"]
            assert note["actor_id"] == actor
            assert note["content"] == "Private reviewer note"
            assert kwargs["return_document"] == (
                svc.ReturnDocument.AFTER
            )
            return {
                "_id": report,
                "admin_notes": [note],
            }

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())

    result = svc.add_case_note(
        actor, report, note_id, "Private reviewer note"
    )
    assert result == {"note_id": note_id, "created": True}


def test_same_note_retry_does_not_write_again(monkeypatch):
    actor, report = ObjectId(), ObjectId()
    note_id = str(uuid4())

    class Reports:
        def find_one_and_update(self, *args, **kwargs):
            return None

        def find_one(self, selector, projection):
            return {
                "admin_notes": [{
                    "note_id": note_id,
                    "actor_id": actor,
                    "content": "Previous reviewer note",
                }]
            }

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())

    result = svc.add_case_note(
        actor, report, note_id, "Previous reviewer note"
    )
    assert result == {"note_id": note_id, "created": False}


def test_note_id_reuse_with_new_content_is_rejected(monkeypatch):
    actor = ObjectId()
    note_id = str(uuid4())

    class Reports:
        def find_one_and_update(self, *args, **kwargs):
            return None

        def find_one(self, selector, projection):
            return {
                "admin_notes": [{
                    "note_id": note_id,
                    "actor_id": actor,
                    "content": "Original content",
                }]
            }

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())

    with pytest.raises(svc.CaseError) as exc:
        svc.add_case_note(
            actor, ObjectId(), note_id, "Changed content"
        )
    assert exc.value.code == "NOTE_CONFLICT"
