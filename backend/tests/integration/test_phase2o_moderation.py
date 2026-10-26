"""Phase 2O-B safe moderation validation."""

import pytest
from bson import ObjectId

from app.services.moderation_case_service import (
    CaseError, decide_case, list_cases,
)


@pytest.mark.parametrize("decision", [
    "ban", "suspend", "submitted", "unknown",
])
def test_rejects_unauthorized_state_transition(decision):
    with pytest.raises(CaseError) as error:
        decide_case(str(ObjectId()), str(ObjectId()), {
            "expected_status": "submitted",
            "decision": decision,
            "reason": "Evidence reviewed",
        })
    assert error.value.code == "INVALID_TRANSITION"


def test_terminal_state_cannot_be_reopened():
    with pytest.raises(CaseError):
        decide_case(str(ObjectId()), str(ObjectId()), {
            "expected_status": "resolved",
            "decision": "in_review",
            "reason": "Invalid transition",
        })


def test_reason_required():
    with pytest.raises(CaseError) as error:
        decide_case(str(ObjectId()), str(ObjectId()), {
            "expected_status": "submitted",
            "decision": "in_review",
            "reason": "",
        })
    assert error.value.code == "INVALID_REASON"


def test_queue_status_rejected():
    with pytest.raises(CaseError):
        list_cases(status="invalid")


def test_queue_limit_rejected():
    with pytest.raises(CaseError):
        list_cases(limit=1000)


def test_actor_injection_rejected():
    with pytest.raises(CaseError) as error:
        decide_case(str(ObjectId()), str(ObjectId()), {
            "expected_status": "submitted",
            "decision": "in_review",
            "reason": "Review",
            "actor_id": str(ObjectId()),
        })
    assert error.value.code == "INVALID_FIELDS"