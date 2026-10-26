"""MongoDB repository for Soultie profile media metadata."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import (
    ASCENDING,
    ReturnDocument,
    UpdateOne,
)

from app.extensions import mongo
from app.schemas.media import (
    MEDIA_STATUS_ACTIVE,
    MEDIA_STATUS_DELETED,
    build_profile_media_document,
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(value)
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        return None


def create_profile_media(
    *,
    user_id,
    kind,
    storage_backend,
    storage_key,
    mime_type,
    size_bytes,
    width,
    height,
    position,
    sha256,
    is_primary=False,
    duration_ms=None,
    thumbnail_storage_key=None,
    thumbnail_mime_type=None,
    thumbnail_width=None,
    thumbnail_height=None,
    now=None,
):
    document = (
        build_profile_media_document(
            user_id=user_id,
            kind=kind,
            storage_backend=storage_backend,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
            position=position,
            sha256=sha256,
            is_primary=is_primary,
            duration_ms=duration_ms,
            thumbnail_storage_key=(
                thumbnail_storage_key
            ),
            thumbnail_mime_type=(
                thumbnail_mime_type
            ),
            thumbnail_width=(
                thumbnail_width
            ),
            thumbnail_height=(
                thumbnail_height
            ),
            now=now,
        )
    )

    result = (
        mongo.db.profile_media.insert_one(
            document
        )
    )

    document["_id"] = (
        result.inserted_id
    )

    return document


def get_media_by_id(
    media_id,
    *,
    include_deleted=False,
):
    object_id = _to_object_id(
        media_id
    )

    if object_id is None:
        return None

    query = {
        "_id": object_id,
    }

    if not include_deleted:
        query["status"] = (
            MEDIA_STATUS_ACTIVE
        )

    return (
        mongo.db.profile_media.find_one(
            query
        )
    )


def get_owned_media(
    user_id,
    media_id,
    *,
    include_deleted=False,
):
    user_object_id = _to_object_id(
        user_id
    )

    media_object_id = _to_object_id(
        media_id
    )

    if (
        user_object_id is None
        or media_object_id is None
    ):
        return None

    query = {
        "_id": media_object_id,
        "user_id": user_object_id,
    }

    if not include_deleted:
        query["status"] = (
            MEDIA_STATUS_ACTIVE
        )

    return (
        mongo.db.profile_media.find_one(
            query
        )
    )


def list_active_profile_media(
    user_id,
):
    user_object_id = _to_object_id(
        user_id
    )

    if user_object_id is None:
        return []

    return list(
        mongo.db.profile_media.find(
            {
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            }
        ).sort(
            [
                (
                    "position",
                    ASCENDING,
                ),
                (
                    "_id",
                    ASCENDING,
                ),
            ]
        )
    )


def count_active_profile_media(
    user_id,
):
    user_object_id = _to_object_id(
        user_id
    )

    if user_object_id is None:
        return 0

    return (
        mongo.db.profile_media.count_documents(
            {
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            }
        )
    )


def get_next_media_position(
    user_id,
):
    user_object_id = _to_object_id(
        user_id
    )

    if user_object_id is None:
        return 0

    last_media = (
        mongo.db.profile_media.find_one(
            {
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            },
            {
                "position": 1,
            },
            sort=[
                (
                    "position",
                    -1,
                )
            ],
        )
    )

    if last_media is None:
        return 0

    return (
        int(
            last_media.get(
                "position",
                -1,
            )
        )
        + 1
    )


def find_active_duplicate_by_hash(
    user_id,
    sha256,
):
    user_object_id = _to_object_id(
        user_id
    )

    if (
        user_object_id is None
        or not isinstance(
            sha256,
            str,
        )
    ):
        return None

    normalized_hash = (
        sha256.strip().lower()
    )

    if not normalized_hash:
        return None

    return (
        mongo.db.profile_media.find_one(
            {
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
                "sha256": normalized_hash,
            }
        )
    )


def get_primary_media(
    user_id,
):
    user_object_id = _to_object_id(
        user_id
    )

    if user_object_id is None:
        return None

    return (
        mongo.db.profile_media.find_one(
            {
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
                "is_primary": True,
            }
        )
    )


def set_primary_media(
    user_id,
    media_id,
    *,
    now=None,
):
    user_object_id = _to_object_id(
        user_id
    )

    media_object_id = _to_object_id(
        media_id
    )

    if (
        user_object_id is None
        or media_object_id is None
    ):
        return None

    target = (
        mongo.db.profile_media.find_one(
            {
                "_id": media_object_id,
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            }
        )
    )

    if target is None:
        return None

    timestamp = (
        now
        if isinstance(
            now,
            datetime,
        )
        else utc_now()
    )

    mongo.db.profile_media.update_many(
        {
            "user_id": user_object_id,
            "status": (
                MEDIA_STATUS_ACTIVE
            ),
            "_id": {
                "$ne": media_object_id,
            },
            "is_primary": True,
        },
        {
            "$set": {
                "is_primary": False,
                "updated_at": timestamp,
            }
        },
    )

    return (
        mongo.db.profile_media.find_one_and_update(
            {
                "_id": media_object_id,
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            },
            {
                "$set": {
                    "is_primary": True,
                    "updated_at": timestamp,
                }
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )


def update_media_positions(
    user_id,
    ordered_media_ids,
    *,
    now=None,
):
    user_object_id = _to_object_id(
        user_id
    )

    if user_object_id is None:
        return False

    object_ids = []

    for media_id in ordered_media_ids:
        object_id = _to_object_id(
            media_id
        )

        if object_id is None:
            return False

        object_ids.append(
            object_id
        )

    if len(
        set(
            object_ids
        )
    ) != len(
        object_ids
    ):
        return False

    if not object_ids:
        return True

    owned_count = (
        mongo.db.profile_media.count_documents(
            {
                "_id": {
                    "$in": object_ids,
                },
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            }
        )
    )

    if owned_count != len(
        object_ids
    ):
        return False

    timestamp = (
        now
        if isinstance(
            now,
            datetime,
        )
        else utc_now()
    )

    operations = [
        UpdateOne(
            {
                "_id": media_id,
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            },
            {
                "$set": {
                    "position": position,
                    "updated_at": timestamp,
                }
            },
        )
        for position, media_id
        in enumerate(
            object_ids
        )
    ]

    result = (
        mongo.db.profile_media.bulk_write(
            operations,
            ordered=True,
        )
    )

    return (
        result.matched_count
        == len(
            object_ids
        )
    )


def mark_media_deleted(
    user_id,
    media_id,
    *,
    now=None,
):
    user_object_id = _to_object_id(
        user_id
    )

    media_object_id = _to_object_id(
        media_id
    )

    if (
        user_object_id is None
        or media_object_id is None
    ):
        return None

    timestamp = (
        now
        if isinstance(
            now,
            datetime,
        )
        else utc_now()
    )

    return (
        mongo.db.profile_media.find_one_and_update(
            {
                "_id": media_object_id,
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_ACTIVE
                ),
            },
            {
                "$set": {
                    "status": (
                        MEDIA_STATUS_DELETED
                    ),
                    "is_primary": False,
                    "deleted_at": timestamp,
                    "updated_at": timestamp,
                }
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )


def restore_media_active(
    user_id,
    media_id,
    *,
    is_primary=False,
    now=None,
):
    """
    Restore metadata during a best-effort rollback.

    This is not a public undelete feature.
    """

    user_object_id = _to_object_id(
        user_id
    )

    media_object_id = _to_object_id(
        media_id
    )

    if (
        user_object_id is None
        or media_object_id is None
    ):
        return None

    timestamp = (
        now
        if isinstance(
            now,
            datetime,
        )
        else utc_now()
    )

    return (
        mongo.db.profile_media.find_one_and_update(
            {
                "_id": media_object_id,
                "user_id": user_object_id,
                "status": (
                    MEDIA_STATUS_DELETED
                ),
            },
            {
                "$set": {
                    "status": (
                        MEDIA_STATUS_ACTIVE
                    ),
                    "is_primary": bool(
                        is_primary
                    ),
                    "updated_at": timestamp,
                },
                "$unset": {
                    "deleted_at": "",
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )


def delete_media_record(
    media_id,
    *,
    user_id=None,
):
    """
    Permanently delete metadata.

    Intended for rollback/cleanup paths, not normal user deletion.
    """

    media_object_id = _to_object_id(
        media_id
    )

    if media_object_id is None:
        return False

    query = {
        "_id": media_object_id,
    }

    if user_id is not None:
        user_object_id = _to_object_id(
            user_id
        )

        if user_object_id is None:
            return False

        query["user_id"] = (
            user_object_id
        )

    result = (
        mongo.db.profile_media.delete_one(
            query
        )
    )

    return (
        result.deleted_count == 1
    )


def normalize_media_positions(
    user_id,
    *,
    now=None,
):
    media = list_active_profile_media(
        user_id
    )

    if not media:
        return []

    ordered_ids = [
        item["_id"]
        for item in media
    ]

    update_media_positions(
        user_id,
        ordered_ids,
        now=now,
    )

    return list_active_profile_media(
        user_id
    )


__all__ = [
    "count_active_profile_media",
    "create_profile_media",
    "delete_media_record",
    "find_active_duplicate_by_hash",
    "get_media_by_id",
    "get_next_media_position",
    "get_owned_media",
    "get_primary_media",
    "list_active_profile_media",
    "mark_media_deleted",
    "normalize_media_positions",
    "restore_media_active",
    "set_primary_media",
    "update_media_positions",
    "utc_now",
]
