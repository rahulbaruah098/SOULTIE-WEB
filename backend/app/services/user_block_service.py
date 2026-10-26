"""User-controlled directional block lifecycle."""

from datetime import datetime, timezone
from bson import ObjectId
from app.extensions import mongo
from app.repositories.user_repository import get_user_by_id
from app.repositories.safety_repository import pair_is_blocked


class BlockError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _id(value):
    if not isinstance(value, (str, ObjectId)):
        raise BlockError("INVALID_USER_ID", "Invalid user ID")
    if not ObjectId.is_valid(str(value)):
        raise BlockError("INVALID_USER_ID", "Invalid user ID")
    return ObjectId(str(value))


def _pair(actor_id, target_id):
    actor = _id(actor_id)
    target = _id(target_id)
    if actor == target:
        raise BlockError("SELF_BLOCK", "Cannot block yourself")
    return actor, target


def block_user(actor_id, target_id):
    actor, target = _pair(actor_id, target_id)

    if get_user_by_id(actor) is None:
        raise BlockError("ACCOUNT_UNAVAILABLE", "Account unavailable", 403)
    if get_user_by_id(target) is None:
        raise BlockError("USER_NOT_FOUND", "User not found", 404)

    now = datetime.now(timezone.utc)
    lookup = {"blocker_id": actor, "blocked_id": target}

    # Upsert is idempotent and backed by the existing unique pair index.
    mongo.db.blocks.update_one(
        lookup,
        {"$setOnInsert": {**lookup, "created_at": now}},
        upsert=True,
    )
    return {
        "blocked_user_id": str(target),
        "blocked": True,
    }


def unblock_user(actor_id, target_id):
    actor, target = _pair(actor_id, target_id)

    # Only this user's directional block may be removed.
    mongo.db.blocks.delete_one({
        "blocker_id": actor,
        "blocked_id": target,
    })
    return {
        "blocked_user_id": str(target),
        "blocked": False,
    }


def list_blocked_users(actor_id, limit=50):
    actor = _id(actor_id)

    try:
        if isinstance(limit, bool):
            raise ValueError()
        limit = int(limit)
    except (ValueError, TypeError):
        raise BlockError("INVALID_LIMIT", "Invalid limit") from None

    if not 1 <= limit <= 100:
        raise BlockError("INVALID_LIMIT", "Limit must be 1 to 100")

    records = mongo.db.blocks.find(
        {"blocker_id": actor},
        {"blocked_id": 1, "_id": 0},
    ).sort("blocked_id", 1).limit(limit)

    return {
        "blocked_user_ids": [
            str(row["blocked_id"]) for row in records
        ]
    }


def pair_blocked(first_id, second_id):
    return bool(pair_is_blocked(_id(first_id), _id(second_id)))
