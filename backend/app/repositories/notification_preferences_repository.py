"""MongoDB persistence for per-user in-app Notification Preferences."""

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.schemas.notification_preferences import (
    build_notification_preferences_document,
)


def _to_object_id(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    if not isinstance(
        value,
        str,
    ):
        return None

    try:
        return ObjectId(
            value.strip()
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        return None


def get_notification_preferences_for_user(
    user_id,
):
    """Return the durable Notification Preferences record owned by a user."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return None

    return (
        mongo.db.notification_preferences.find_one(
            {
                "user_id": user_object_id,
            }
        )
    )


def ensure_notification_preferences_for_user(
    user_id,
    *,
    now=None,
):
    """Atomically create default preferences when a user has none."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return None

    document = (
        build_notification_preferences_document(
            user_id=user_object_id,
            now=now,
        )
    )

    try:
        return (
            mongo.db.notification_preferences.find_one_and_update(
                {
                    "user_id": user_object_id,
                },
                {
                    "$setOnInsert": document,
                },
                upsert=True,
                return_document=(
                    ReturnDocument.AFTER
                ),
            )
        )

    except DuplicateKeyError:
        # Once the unique user_id index exists, simultaneous first-use
        # requests can race. The winning record is the canonical one.
        existing = (
            get_notification_preferences_for_user(
                user_object_id
            )
        )

        if existing is None:
            raise

        return existing


def update_notification_preferences_for_user(
    user_id,
    *,
    disabled_types,
    now=None,
):
    """Upsert the canonical user-owned in-app Notification Preferences."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return None

    candidate = (
        build_notification_preferences_document(
            user_id=user_object_id,
            disabled_types=disabled_types,
            now=now,
        )
    )

    query = {
        "user_id": user_object_id,
    }

    update = {
        "$set": {
            "schema_version": (
                candidate[
                    "schema_version"
                ]
            ),
            "disabled_types": (
                candidate[
                    "disabled_types"
                ]
            ),
            "updated_at": (
                candidate[
                    "updated_at"
                ]
            ),
        },
        "$setOnInsert": {
            "user_id": user_object_id,
            "created_at": (
                candidate[
                    "created_at"
                ]
            ),
        },
    }

    try:
        return (
            mongo.db.notification_preferences.find_one_and_update(
                query,
                update,
                upsert=True,
                return_document=(
                    ReturnDocument.AFTER
                ),
            )
        )

    except DuplicateKeyError:
        # Retry against the record created by the concurrent winner.
        updated = (
            mongo.db.notification_preferences.find_one_and_update(
                query,
                {
                    "$set": update[
                        "$set"
                    ],
                },
                return_document=(
                    ReturnDocument.AFTER
                ),
            )
        )

        if updated is None:
            raise

        return updated


__all__ = [
    "ensure_notification_preferences_for_user",
    "get_notification_preferences_for_user",
    "update_notification_preferences_for_user",
]
