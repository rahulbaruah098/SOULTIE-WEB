"""Phase 2O-C validation and privacy tests."""

from datetime import datetime, timezone
from bson import ObjectId
import pytest

from app.services.report_appeal_service import (
    AppealError, appeal_reason, public_appeal,
    list_appeals, review_appeal,
)


@pytest.mark.parametrize("reason", [
    "", "short", "x" * 2001, 123, None
])
def test_invalid_appeal_reason(reason):
    with pytest.raises(AppealError):
        appeal_reason(reason)


def test_valid_appeal_reason():
    assert appeal_reason("  I have additional evidence  ") == (
        "I have additional evidence"
    )


def test_appeal_serialization_is_private():
    row = {
        "_id": ObjectId(),
        "report_id": ObjectId(),
        "appellant_id": ObjectId(),
        "reason": "Additional evidence regarding my report",
        "status": "submitted",
        "created_at": datetime.now(timezone.utc),
        "history": [{"actor_id": ObjectId()}],
    }
    result = public_appeal(row)
    assert "appellant_id" not in result
    assert "history" not in result


def test_unknown_status_rejected():
    with pytest.raises(AppealError):
        list_appeals("unknown")


def test_invalid_review_decision_rejected():
    with pytest.raises(AppealError):
        review_appeal(
            str(ObjectId()),
            str(ObjectId()),
            "delete_account",
            "I have reviewed the case",
        )