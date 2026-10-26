
"""Phase 2O private case-note retrieval."""

from datetime import datetime, timezone
from uuid import uuid4
from bson import ObjectId
import pytest

from app.services import moderation_case_service as svc


def test_private_note_serialization(monkeypatch):
    actor, report = ObjectId(), ObjectId()
    note_id = str(uuid4())
    now = datetime.now(timezone.utc)

    class Reports:
        def find_one(self, selector, projection):
            assert selector == {"_id": report}
            assert projection == {"admin_notes": 1}
            return {"admin_notes": [{
                "note_id": note_id,
                "actor_id": actor,
                "content": "Restricted case note",
                "created_at": now,
            }]}

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())

    data = svc.list_case_notes(actor, report)
    assert data["report_id"] == str(report)
    assert data["notes"][0]["reviewer_id"] == str(actor)
    assert data["notes"][0]["content"] == "Restricted case note"


@pytest.mark.parametrize("limit", [0, 101, True, "bad"])
def test_invalid_limits_fail_closed(limit):
    with pytest.raises(svc.CaseError):
        svc.list_case_notes(ObjectId(), ObjectId(), limit)


def test_missing_report_rejected(monkeypatch):
    class Reports:
        def find_one(self, selector, projection):
            return None

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())
    with pytest.raises(svc.CaseError) as exc:
        svc.list_case_notes(ObjectId(), ObjectId())
    assert exc.value.code == "CASE_NOT_FOUND"


def test_corrupt_note_data_rejected(monkeypatch):
    class Reports:
        def find_one(self, selector, projection):
            return {"admin_notes": [{"content": "missing metadata"}]}

    class DB:
        reports = Reports()

    class Mongo:
        db = DB()

    monkeypatch.setattr(svc, "mongo", Mongo())
    with pytest.raises(svc.CaseError) as exc:
        svc.list_case_notes(ObjectId(), ObjectId())
    assert exc.value.code == "INVALID_CASE"


def test_anonymous_notes_are_denied():
    from flask import Flask
    from app.api.v1 import api_v1_bp

    app = Flask("phase2o-notes-denial")
    app.config.update(TESTING=True, SECRET_KEY="test-only")
    app.register_blueprint(api_v1_bp)

    response = app.test_client().get(
        "/api/v1/report-moderation/cases/"
        + str(ObjectId())
        + "/notes"
    )
    assert response.status_code in (401, 403)
