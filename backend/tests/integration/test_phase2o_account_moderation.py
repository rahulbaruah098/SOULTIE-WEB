
"""Phase 2O account moderation validation and atomicity tests."""

from bson import ObjectId
import pytest

from app.services import account_moderation_service as service


@pytest.mark.parametrize("expected,decision", [
    ("active", "active"),
    ("banned", "suspended"),
    ("deleted", "active"),
    ("active", "deleted"),
])
def test_invalid_transitions(expected, decision):
    with pytest.raises(service.AccountModerationError):
        service.validate_action(
            expected, decision, "A documented moderator decision"
        )


@pytest.mark.parametrize("expected,decision", [
    ("active", "suspended"),
    ("active", "banned"),
    ("suspended", "active"),
    ("suspended", "banned"),
    ("banned", "active"),
])
def test_supported_transitions(expected, decision):
    result = service.validate_action(
        expected, decision, "A documented moderator decision"
    )
    assert result[:2] == (expected, decision)


def test_no_status_change_without_reason():
    with pytest.raises(service.AccountModerationError):
        service.validate_action("active", "suspended", "")


def test_report_link_must_match_target(monkeypatch):
    class Reports:
        def find_one(self, query, projection):
            return None

    class Users:
        def find_one_and_update(self, *args, **kwargs):
            raise AssertionError("User must not be modified")

    class DB:
        reports = Reports()
        users = Users()

    class Mongo:
        db = DB()

    monkeypatch.setattr(service, "mongo", Mongo())

    with pytest.raises(service.AccountModerationError) as exc:
        service.moderate_account(
            ObjectId(), ObjectId(), "active", "banned",
            "Verified severe account abuse", report_id=ObjectId(),
        )
    assert exc.value.code == "REPORT_NOT_FOUND"


def test_status_change_and_audit_use_single_atomic_update(monkeypatch):
    target = ObjectId()
    actor = ObjectId()

    class Users:
        def find_one_and_update(self, query, update, **kwargs):
            assert query == {
                "_id": target,
                "account_status": "active",
            }
            assert update["$set"]["account_status"] == "suspended"
            event = update["$push"]["account_moderation_history"]
            assert event["moderator_id"] == actor
            assert event["from_status"] == "active"
            assert event["to_status"] == "suspended"
            assert kwargs["return_document"] == service.ReturnDocument.AFTER
            return {"_id": target, "account_status": "suspended"}

    class DB:
        users = Users()

    class Mongo:
        db = DB()

    monkeypatch.setattr(service, "mongo", Mongo())

    result = service.moderate_account(
        actor, target, "active", "suspended",
        "Repeated confirmed policy violations",
    )
    assert result == {
        "user_id": str(target),
        "account_status": "suspended",
    }


def test_conflicting_account_status_fails_closed(monkeypatch):
    class Users:
        def find_one_and_update(self, *args, **kwargs):
            return None

    class DB:
        users = Users()

    class Mongo:
        db = DB()

    monkeypatch.setattr(service, "mongo", Mongo())

    with pytest.raises(service.AccountModerationError) as exc:
        service.moderate_account(
            ObjectId(), ObjectId(), "active", "banned",
            "Confirmed serious policy violation",
        )
    assert exc.value.code == "ACCOUNT_CONFLICT"
