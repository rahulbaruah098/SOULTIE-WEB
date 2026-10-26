"""Phase 2O-D user block boundary tests."""

import pytest
from bson import ObjectId
from app.services import user_block_service as blocks


def test_self_block_is_rejected():
    user = ObjectId()
    with pytest.raises(blocks.BlockError) as exc:
        blocks._pair(user, user)
    assert exc.value.code == "SELF_BLOCK"


@pytest.mark.parametrize("value", ["", "wrong", None, 123])
def test_invalid_user_identifiers_fail_closed(value):
    with pytest.raises(blocks.BlockError):
        blocks._id(value)


def test_pair_normalization():
    a, b = ObjectId(), ObjectId()
    assert blocks._pair(str(a), str(b)) == (a, b)


@pytest.mark.parametrize("limit", [0, 101, True, "wrong"])
def test_invalid_list_limits_fail_before_db(limit):
    with pytest.raises(blocks.BlockError):
        blocks.list_blocked_users(ObjectId(), limit)


def test_block_and_unblock_owner_fields_are_directional(monkeypatch):
    class Collection:
        def __init__(self):
            self.upsert_filter = None
            self.delete_filter = None
        def update_one(self, query, update, upsert=False):
            self.upsert_filter = query
            assert upsert is True
            assert "$setOnInsert" in update
        def delete_one(self, query):
            self.delete_filter = query

    class DB:
        blocks = Collection()

    class Mongo:
        db = DB()

    monkeypatch.setattr(blocks, "mongo", Mongo())
    monkeypatch.setattr(
        blocks, "get_user_by_id", lambda user_id: {"_id": user_id}
    )

    actor, target = ObjectId(), ObjectId()
    result = blocks.block_user(actor, target)
    assert result["blocked"] is True
    assert blocks.mongo.db.blocks.upsert_filter == {
        "blocker_id": actor, "blocked_id": target
    }

    result = blocks.unblock_user(actor, target)
    assert result["blocked"] is False
    assert blocks.mongo.db.blocks.delete_filter == {
        "blocker_id": actor, "blocked_id": target
    }


def test_existing_pair_block_repository_is_reused(monkeypatch):
    a, b = ObjectId(), ObjectId()
    monkeypatch.setattr(
        blocks, "pair_is_blocked",
        lambda first, second: first == a and second == b,
    )
    assert blocks.pair_blocked(a, b) is True
