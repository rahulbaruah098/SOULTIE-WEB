"""Phase 2O blocked-pair Safe Meet acceptance."""

from unittest.mock import patch
import pytest
from bson import ObjectId
from app.services import safe_meet_service as service


def plan_for(first, second):
    return {
        "participant_ids": [str(first), str(second)],
        "conversation_id": str(ObjectId()),
    }


def test_blocked_pair_is_denied():
    first, second = ObjectId(), ObjectId()

    with patch(
        "app.services.chat_service.get_my_conversation",
        return_value={},
    ), patch(
        "app.repositories.safety_repository.pair_is_blocked",
        return_value=True,
    ) as checker:
        with pytest.raises(service.SafeMeetAuthorizationError):
            service._check_plan_access(plan_for(first, second), first)

        checker.assert_called_once_with(first, second)


def test_unblocked_pair_is_allowed():
    first, second = ObjectId(), ObjectId()
    plan = plan_for(first, second)

    with patch(
        "app.services.chat_service.get_my_conversation",
        return_value={},
    ), patch(
        "app.repositories.safety_repository.pair_is_blocked",
        return_value=False,
    ):
        assert service._check_plan_access(plan, first) is plan


def test_nonparticipant_is_denied():
    first, second, stranger = ObjectId(), ObjectId(), ObjectId()

    with pytest.raises(service.SafeMeetAuthorizationError):
        service._check_plan_access(plan_for(first, second), stranger)


def test_corrupt_participant_is_denied_without_unhandled_exception():
    first = ObjectId()
    plan = {
        "participant_ids": [str(first), "not-an-object-id"],
        "conversation_id": str(ObjectId()),
    }

    with patch(
        "app.services.chat_service.get_my_conversation",
        return_value={},
    ):
        with pytest.raises(service.SafeMeetAuthorizationError):
            service._check_plan_access(plan, first)
