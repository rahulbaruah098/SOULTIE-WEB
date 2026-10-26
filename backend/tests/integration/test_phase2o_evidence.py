"""Report evidence ownership and authorization contracts."""

from unittest.mock import patch
from bson import ObjectId
import pytest

from app.services import report_service as service


def test_report_with_no_evidence_is_allowed():
    service.validate_evidence_ownership(
        ObjectId(), ObjectId(), []
    )


@pytest.mark.parametrize("reference", [
    "https://example.com/photo.jpg",
    "private:123",
    "message:invalid",
    "image:507f1f77bcf86cd799439011",
])
def test_unsupported_evidence_is_rejected(reference):
    with pytest.raises(service.ReportError):
        service.validate_evidence_ownership(
            ObjectId(), ObjectId(), [reference]
        )


def test_message_evidence_must_include_both_participants():
    reporter, target, stranger = ObjectId(), ObjectId(), ObjectId()
    message_id, conversation_id = ObjectId(), ObjectId()

    with patch(
        "app.repositories.chat_repository.get_message_by_id",
        return_value={"_id": message_id, "conversation_id": conversation_id},
    ), patch(
        "app.repositories.chat_repository.get_conversation_by_id",
        return_value={
            "_id": conversation_id,
            "member_ids": [reporter, stranger],
        },
    ):
        with pytest.raises(service.ReportError) as error:
            service.validate_evidence_ownership(
                reporter, target, ["message:" + str(message_id)]
            )
        assert error.value.code == "EVIDENCE_NOT_AVAILABLE"


def test_owned_message_evidence_is_allowed():
    reporter, target = ObjectId(), ObjectId()
    message_id, conversation_id = ObjectId(), ObjectId()

    with patch(
        "app.repositories.chat_repository.get_message_by_id",
        return_value={"_id": message_id, "conversation_id": conversation_id},
    ), patch(
        "app.repositories.chat_repository.get_conversation_by_id",
        return_value={
            "_id": conversation_id,
            "member_ids": [reporter, target],
        },
    ):
        service.validate_evidence_ownership(
            reporter, target, ["message:" + str(message_id)]
        )


def test_missing_message_fails_closed():
    with patch(
        "app.repositories.chat_repository.get_message_by_id",
        return_value=None,
    ):
        with pytest.raises(service.ReportError) as error:
            service.validate_evidence_ownership(
                ObjectId(), ObjectId(),
                ["message:" + str(ObjectId())],
            )
        assert error.value.code == "EVIDENCE_NOT_AVAILABLE"


def test_missing_conversation_fails_closed():
    message_id = ObjectId()
    with patch(
        "app.repositories.chat_repository.get_message_by_id",
        return_value={"conversation_id": ObjectId()},
    ), patch(
        "app.repositories.chat_repository.get_conversation_by_id",
        return_value=None,
    ):
        with pytest.raises(service.ReportError):
            service.validate_evidence_ownership(
                ObjectId(), ObjectId(),
                ["message:" + str(message_id)],
            )
