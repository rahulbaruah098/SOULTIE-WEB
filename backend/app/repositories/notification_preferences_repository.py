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



_UNSET_NOTIFICATION_PREFERENCE = object()



def update_notification_preferences_for_user(
    user_id,
    *,
    disabled_types=_UNSET_NOTIFICATION_PREFERENCE,
    email_enabled=_UNSET_NOTIFICATION_PREFERENCE,
    push_enabled=_UNSET_NOTIFICATION_PREFERENCE,
    now=None,
):
    """Atomically patch canonical user-owned Notification Preferences."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return None

    if (
        disabled_types
        is _UNSET_NOTIFICATION_PREFERENCE
        and email_enabled
        is _UNSET_NOTIFICATION_PREFERENCE
        and push_enabled
        is _UNSET_NOTIFICATION_PREFERENCE
    ):
        raise ValueError(
            "At least one Notification Preferences "
            "field is required."
        )

    candidate = (
        build_notification_preferences_document(
            user_id=user_object_id,
            disabled_types=(
                []
                if disabled_types
                is _UNSET_NOTIFICATION_PREFERENCE
                else disabled_types
            ),
            now=now,
        )
    )

    if (
        email_enabled
        is not _UNSET_NOTIFICATION_PREFERENCE
        and not isinstance(
            email_enabled,
            bool,
        )
    ):
        raise ValueError(
            "email_enabled must be true or false."
        )

    if (
        push_enabled
        is not _UNSET_NOTIFICATION_PREFERENCE
        and not isinstance(
            push_enabled,
            bool,
        )
    ):
        raise ValueError(
            "push_enabled must be true or false."
        )

    set_fields = {
        "schema_version": (
            candidate[
                "schema_version"
            ]
        ),
        "updated_at": (
            candidate[
                "updated_at"
            ]
        ),
    }

    if (
        disabled_types
        is not _UNSET_NOTIFICATION_PREFERENCE
    ):
        set_fields[
            "disabled_types"
        ] = (
            candidate[
                "disabled_types"
            ]
        )

    if (
        email_enabled
        is not _UNSET_NOTIFICATION_PREFERENCE
    ):
        set_fields[
            "email_enabled"
        ] = email_enabled

    if (
        push_enabled
        is not _UNSET_NOTIFICATION_PREFERENCE
    ):
        set_fields[
            "push_enabled"
        ] = push_enabled

    set_on_insert = {
        "user_id": user_object_id,
        "created_at": (
            candidate[
                "created_at"
            ]
        ),
    }

    # Preserve the original in-app default only. Background channel
    # defaults remain policy defaults until explicitly configured.
    if (
        disabled_types
        is _UNSET_NOTIFICATION_PREFERENCE
    ):
        set_on_insert[
            "disabled_types"
        ] = []

    query = {
        "user_id": user_object_id,
    }

    update = {
        "$set": set_fields,
        "$setOnInsert": set_on_insert,
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
        updated = (
            mongo.db.notification_preferences.find_one_and_update(
                query,
                {
                    "$set": set_fields,
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
