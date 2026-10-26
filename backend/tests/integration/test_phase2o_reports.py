"""Phase 2O reporting contract tests."""

from uuid import uuid4
from bson import ObjectId
import pytest

from app.services.report_service import (
    ReportError, validate_report, serialize_report,
)


def make_payload(target):
    return {
        "reported_user_id": str(target),
        "reason": "harassment",
        "idempotency_key": str(uuid4()),
    }


def test_valid_report():
    a, b = ObjectId(), ObjectId()
    result = validate_report(a, make_payload(b))
    assert result[0] == a
    assert result[1] == b


def test_self_report_rejected():
    a = ObjectId()
    with pytest.raises(ReportError) as exc:
        validate_report(a, make_payload(a))
    assert exc.value.code == "SELF_REPORT"


def test_admin_field_injection_rejected():
    a, b = ObjectId(), ObjectId()
    payload = make_payload(b)
    payload["status"] = "resolved"
    with pytest.raises(ReportError):
        validate_report(a, payload)


def test_evidence_url_rejected():
    a, b = ObjectId(), ObjectId()
    payload = make_payload(b)
    payload["evidence_refs"] = ["https://example.org/file"]
    with pytest.raises(ReportError):
        validate_report(a, payload)


def test_invalid_idempotency_key_rejected():
    a, b = ObjectId(), ObjectId()
    payload = make_payload(b)
    payload["idempotency_key"] = "duplicate"
    with pytest.raises(ReportError):
        validate_report(a, payload)


def test_private_serializer():
    from datetime import datetime, timezone
    item = {
        "_id": ObjectId(),
        "reporter_id": ObjectId(),
        "target_user_id": ObjectId(),
        "reason": "spam",
        "description": "",
        "status": "submitted",
        "evidence_refs": ["private:123"],
        "created_at": datetime.now(timezone.utc),
    }
    result = serialize_report(item)
    assert "reporter_id" not in result
    assert "evidence_refs" not in result
    assert result["status"] == "submitted"
