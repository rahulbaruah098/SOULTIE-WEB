"""Accepted appeal transaction boundary tests."""

from unittest.mock import MagicMock, patch
from bson import ObjectId
import pytest

from app.services import report_appeal_service as service


def test_reject_decision_does_not_require_transaction():
    admin = ObjectId()
    appeal_id = ObjectId()
    row = {
        "_id": appeal_id,
        "report_id": ObjectId(),
        "status": "rejected",
        "reason": "Original appeal statement",
        "created_at": service.datetime.now(service.timezone.utc),
    }
    with patch.object(service, "mongo") as mongo:
        mongo.db.report_appeals.find_one_and_update.return_value = row
        result = service.review_appeal(
            admin, appeal_id, "rejected",
            "The evidence does not change the outcome",
        )
        assert result["status"] == "rejected"
        mongo.cx.start_session.assert_not_called()


def test_accepted_appeal_requires_transaction():
    admin = ObjectId()
    appeal_id = ObjectId()
    report_id = ObjectId()
    appellant_id = ObjectId()
    row = {
        "_id": appeal_id,
        "report_id": report_id,
        "appellant_id": appellant_id,
        "status": "submitted",
        "reason": "Additional evidence was submitted",
        "created_at": service.datetime.now(service.timezone.utc),
    }
    accepted = dict(row, status="accepted")

    with patch.object(service, "mongo") as mongo:
        mongo.db.report_appeals.find_one.return_value = row
        mongo.db.reports.update_one.return_value.matched_count = 1
        mongo.db.report_appeals.find_one_and_update.return_value = accepted
        result = service.review_appeal(
            admin, appeal_id, "accepted",
            "The appeal provides material new evidence",
        )
        assert result["status"] == "accepted"
        mongo.cx.start_session.assert_called_once()
        mongo.db.reports.update_one.assert_called_once()
        kwargs = mongo.db.reports.update_one.call_args.kwargs
        assert "session" in kwargs
        assert mongo.db.report_appeals.find_one_and_update.call_args.kwargs[
            "session"
        ] is kwargs["session"]


def test_report_conflict_aborts_accepted_appeal():
    admin = ObjectId()
    with patch.object(service, "mongo") as mongo:
        mongo.db.report_appeals.find_one.return_value = {
            "_id": ObjectId(),
            "report_id": ObjectId(),
            "appellant_id": ObjectId(),
        }
        mongo.db.reports.update_one.return_value.matched_count = 0
        with pytest.raises(service.AppealError) as exc:
            service.review_appeal(
                admin, ObjectId(), "accepted",
                "The decision requires another review",
            )
        assert exc.value.code == "REPORT_CONFLICT"
        mongo.db.report_appeals.find_one_and_update.assert_not_called()
