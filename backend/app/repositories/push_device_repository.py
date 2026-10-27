"""Durable, user-scoped provider-neutral push devices."""

from datetime import datetime, timezone
from hashlib import sha256

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo


class PushTokenInUse(Exception):
    pass


def _owner(user_id):
    if isinstance(user_id, ObjectId):
        return user_id
    try:
        return ObjectId(str(user_id))
    except Exception as exc:
        raise ValueError("Invalid user ID.") from exc


def _hash_token(token):
    return sha256(token.encode("utf-8")).hexdigest()


def register_push_device(
    *,
    user_id,
    device_id,
    platform,
    push_provider,
    push_token,
    device_name=None,
    family_id=None,
):
    owner = _owner(user_id)
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(push_token)

    query = {
        "user_id": owner,
        "device_id": device_id,
    }

    changes = {
        "$set": {
            "platform": platform,
            "push_provider": push_provider,
            "push_token": push_token,
            "push_token_hash": token_hash,
            "push_enabled": True,
            "revoked_at": None,
            "device_name": device_name,
            "session_family_id": family_id,
            "token_updated_at": now,
            "last_seen_at": now,
            "updated_at": now,
        },
        "$setOnInsert": {
            "user_id": owner,
            "device_id": device_id,
            "created_at": now,
        },
    }

    try:
        document = mongo.db.user_devices.find_one_and_update(
            query,
            changes,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        # Includes conflicts with another account's active token.
        raise PushTokenInUse() from exc

    return document


def list_user_push_devices(user_id):
    return list(
        mongo.db.user_devices.find(
            {
                "user_id": _owner(user_id),
                "push_enabled": True,
                "revoked_at": None,
            }
        ).sort("created_at", -1)
    )


def revoke_user_push_device(*, user_id, device_id):
    now = datetime.now(timezone.utc)
    return mongo.db.user_devices.find_one_and_update(
        {
            "user_id": _owner(user_id),
            "device_id": device_id,
            "push_enabled": True,
            "revoked_at": None,
        },
        {
            "$set": {
                "push_enabled": False,
                "revoked_at": now,
                "updated_at": now,
            },
            "$unset": {
                "push_token": "",
                "push_token_hash": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )


def revoke_family_push_devices(*, user_id, family_id):
    """Clear only tokens registered against the logged-out session family."""
    if not isinstance(family_id, str) or not family_id:
        return 0
    now = datetime.now(timezone.utc)
    result = mongo.db.user_devices.update_many(
        {
            "user_id": _owner(user_id),
            "session_family_id": family_id,
            "push_enabled": True,
            "revoked_at": None,
        },
        {
            "$set": {
                "push_enabled": False,
                "revoked_at": now,
                "updated_at": now,
            },
            "$unset": {"push_token": "", "push_token_hash": ""},
        },
    )
    return result.modified_count
