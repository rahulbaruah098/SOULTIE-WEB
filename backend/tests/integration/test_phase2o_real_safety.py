"""Phase 2O real MongoDB evidence and block acceptance.

Creates and drops a uniquely named test database only.
Requires a locally accessible test MongoDB on 127.0.0.1.
"""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from app.repositories import chat_repository, safety_repository
from app.services import report_service, safe_meet_service


@pytest.fixture
def isolated_db(monkeypatch):
    name = "st2os_" + uuid4().hex
    assert len(name) <= 63

    client = MongoClient(
        "mongodb://127.0.0.1:27017/",
        serverSelectionTimeoutMS=3000,
    )

    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("Local test MongoDB unavailable")

    database = client[name]

    class IsolatedMongo:
        db = database

    monkeypatch.setattr(chat_repository, "mongo", IsolatedMongo())
    monkeypatch.setattr(safety_repository, "mongo", IsolatedMongo())

    try:
        yield database
    finally:
        try:
            assert name.startswith("st2os_")
            client.drop_database(name)
        finally:
            client.close()


def test_real_message_evidence_requires_both_participants(isolated_db):
    reporter, target, stranger = ObjectId(), ObjectId(), ObjectId()
    conversation = ObjectId()
    message = ObjectId()
    now = datetime.now(timezone.utc)

    isolated_db.conversations.insert_one({
        "_id": conversation,
        "member_ids": [reporter, target],
        "created_at": now,
    })
    isolated_db.messages.insert_one({
        "_id": message,
        "conversation_id": conversation,
        "sender_id": target,
        "body": "Evidence-bearing message",
        "created_at": now,
    })

    ref = "message:" + str(message)
    report_service.validate_evidence_ownership(
        reporter, target, [ref]
    )

    with pytest.raises(report_service.ReportError):
        report_service.validate_evidence_ownership(
            reporter, stranger, [ref]
        )

    print("REAL_EVIDENCE_OWNERSHIP_PASSED")


def test_real_symmetric_block_enforcement(isolated_db):
    first, second = ObjectId(), ObjectId()

    isolated_db.blocks.insert_one({
        "blocker_id": second,
        "blocked_id": first,
    })

    assert safety_repository.pair_is_blocked(first, second)
    assert safety_repository.pair_is_blocked(second, first)

    isolated_db.blocks.delete_many({})

    assert not safety_repository.pair_is_blocked(first, second)
    print("REAL_SYMMETRIC_BLOCK_ENFORCEMENT_PASSED")


def test_real_block_prevents_safe_meet_access(isolated_db):
    first, second = ObjectId(), ObjectId()

    isolated_db.blocks.insert_one({
        "blocker_id": second,
        "blocked_id": first,
    })

    plan = {
        "participant_ids": [str(first), str(second)],
        "conversation_id": str(ObjectId()),
    }

    with patch(
        "app.services.chat_service.get_my_conversation",
        return_value={"id": plan["conversation_id"]},
    ):
        with pytest.raises(
            safe_meet_service.SafeMeetAuthorizationError
        ):
            safe_meet_service._check_plan_access(plan, first)

    print("REAL_BLOCKED_SAFE_MEET_DENIAL_PASSED")