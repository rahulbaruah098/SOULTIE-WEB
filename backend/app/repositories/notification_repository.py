"""MongoDB persistence for durable Xuoroni notifications."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import (
    DESCENDING,
    ReturnDocument,
)
from pymongo.errors import (
    DuplicateKeyError,
)

from app.extensions import mongo
from app.schemas.notification import (
    build_notification_document,
)


DEFAULT_NOTIFICATION_PAGE_SIZE = 20
MAX_NOTIFICATION_PAGE_SIZE = 50


def utc_now():
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


def _normalize_page_size(
    value,
):
    if isinstance(
        value,
        bool,
    ):
        raise ValueError(
            "Invalid notification page size."
        )

    try:
        parsed = int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "Invalid notification page size."
        ) from None

    if not (
        1
        <= parsed
        <= MAX_NOTIFICATION_PAGE_SIZE
    ):
        raise ValueError(
            "Invalid notification page size."
        )

    return parsed


def _normalize_boolean(
    value,
    field,
):
    if not isinstance(
        value,
        bool,
    ):
        raise ValueError(
            f"Invalid {field}."
        )

    return value


def _normalize_timestamp(
    value,
    field,
):
    if not isinstance(
        value,
        datetime,
    ):
        raise ValueError(
            f"Invalid {field}."
        )

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _normalize_cursor(
    cursor,
):
    if cursor is None:
        return None

    if not isinstance(
        cursor,
        dict,
    ):
        raise ValueError(
            "Invalid notification cursor."
        )

    created_at = cursor.get(
        "created_at"
    )

    notification_id = (
        _to_object_id(
            cursor.get(
                "notification_id"
            )
        )
    )

    if (
        not isinstance(
            created_at,
            datetime,
        )
        or notification_id is None
    ):
        raise ValueError(
            "Invalid notification cursor."
        )

    return {
        "created_at": (
            _normalize_timestamp(
                created_at,
                "notification cursor timestamp",
            )
        ),
        "notification_id": (
            notification_id
        ),
    }


def _find_notification_by_dedupe_key(
    user_id,
    dedupe_key,
):
    if dedupe_key is None:
        return None

    return (
        mongo.db.notifications.find_one(
            {
                "user_id": user_id,
                "dedupe_key": (
                    dedupe_key
                ),
            }
        )
    )


def _same_notification_identity(
    existing,
    candidate,
):
    if not isinstance(
        existing,
        dict,
    ):
        return False

    fields = (
        "type",
        "resource_type",
        "resource_id",
    )

    return all(
        existing.get(
            field
        )
        == candidate.get(
            field
        )
        for field in fields
    )


def _idempotent_existing_result(
    existing,
    candidate,
):
    if existing is None:
        return None

    if not _same_notification_identity(
        existing,
        candidate,
    ):
        raise ValueError(
            (
                "Notification dedupe key was "
                "already used for a different "
                "notification."
            )
        )

    return {
        "notification": existing,
        "created": False,
    }


def create_notification_idempotent(
    *,
    user_id,
    notification_type,
    resource_type=None,
    resource_id=None,
    dedupe_key=None,
    now=None,
):
    """Create one durable notification with optional retry safety."""

    document = (
        build_notification_document(
            user_id=user_id,
            notification_type=(
                notification_type
            ),
            resource_type=(
                resource_type
            ),
            resource_id=(
                resource_id
            ),
            dedupe_key=(
                dedupe_key
            ),
            now=now,
        )
    )

    if (
        document[
            "dedupe_key"
        ]
        is not None
    ):
        existing = (
            _find_notification_by_dedupe_key(
                document[
                    "user_id"
                ],
                document[
                    "dedupe_key"
                ],
            )
        )

        result = (
            _idempotent_existing_result(
                existing,
                document,
            )
        )

        if result is not None:
            return result

    try:
        inserted = (
            mongo.db.notifications.insert_one(
                document
            )
        )

    except DuplicateKeyError:
        if (
            document[
                "dedupe_key"
            ]
            is None
        ):
            raise

        existing = (
            _find_notification_by_dedupe_key(
                document[
                    "user_id"
                ],
                document[
                    "dedupe_key"
                ],
            )
        )

        result = (
            _idempotent_existing_result(
                existing,
                document,
            )
        )

        if result is None:
            raise

        return result

    created = (
        mongo.db.notifications.find_one(
            {
                "_id": (
                    inserted.inserted_id
                ),
                "user_id": (
                    document[
                        "user_id"
                    ]
                ),
            }
        )
    )

    return {
        "notification": created,
        "created": True,
    }


def get_notification_by_id_for_user(
    notification_id,
    user_id,
):
    notification_object_id = (
        _to_object_id(
            notification_id
        )
    )

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if (
        notification_object_id is None
        or user_object_id is None
    ):
        return None

    return (
        mongo.db.notifications.find_one(
            {
                "_id": (
                    notification_object_id
                ),
                "user_id": (
                    user_object_id
                ),
            }
        )
    )


def list_notifications_for_user(
    user_id,
    *,
    limit=DEFAULT_NOTIFICATION_PAGE_SIZE,
    cursor=None,
    unread_only=False,
    include_dismissed=False,
):
    """Fetch one recipient-scoped deterministic Notification page."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return {
            "notifications": [],
            "has_more": False,
        }

    parsed_limit = (
        _normalize_page_size(
            limit
        )
    )

    unread_only = (
        _normalize_boolean(
            unread_only,
            "unread-only flag",
        )
    )

    include_dismissed = (
        _normalize_boolean(
            include_dismissed,
            "include-dismissed flag",
        )
    )

    normalized_cursor = (
        _normalize_cursor(
            cursor
        )
    )

    clauses = [
        {
            "user_id": (
                user_object_id
            ),
        }
    ]

    if unread_only:
        clauses.append(
            {
                "read_at": None,
            }
        )

    if not include_dismissed:
        clauses.append(
            {
                "dismissed_at": None,
            }
        )

    if normalized_cursor is not None:
        clauses.append(
            {
                "$or": [
                    {
                        "created_at": {
                            "$lt": (
                                normalized_cursor[
                                    "created_at"
                                ]
                            ),
                        }
                    },
                    {
                        "created_at": (
                            normalized_cursor[
                                "created_at"
                            ]
                        ),
                        "_id": {
                            "$lt": (
                                normalized_cursor[
                                    "notification_id"
                                ]
                            ),
                        },
                    },
                ]
            }
        )

    if len(
        clauses
    ) == 1:
        query = clauses[0]

    else:
        query = {
            "$and": clauses,
        }

    documents = list(
        mongo.db.notifications.find(
            query
        )
        .sort(
            [
                (
                    "created_at",
                    DESCENDING,
                ),
                (
                    "_id",
                    DESCENDING,
                ),
            ]
        )
        .limit(
            parsed_limit
            + 1
        )
    )

    has_more = (
        len(
            documents
        )
        > parsed_limit
    )

    if has_more:
        documents = documents[
            :parsed_limit
        ]

    return {
        "notifications": documents,
        "has_more": has_more,
    }


def count_unread_notifications(
    user_id,
):
    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return 0

    return (
        mongo.db.notifications.count_documents(
            {
                "user_id": (
                    user_object_id
                ),
                "read_at": None,
                "dismissed_at": None,
            }
        )
    )


def mark_notification_read(
    notification_id,
    user_id,
    *,
    now=None,
):
    """Mark one owned notification read without cross-user access."""

    notification_object_id = (
        _to_object_id(
            notification_id
        )
    )

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if (
        notification_object_id is None
        or user_object_id is None
    ):
        return {
            "notification": None,
            "changed": False,
        }

    timestamp = (
        utc_now()
        if now is None
        else _normalize_timestamp(
            now,
            "notification read timestamp",
        )
    )

    updated = (
        mongo.db.notifications.find_one_and_update(
            {
                "_id": (
                    notification_object_id
                ),
                "user_id": (
                    user_object_id
                ),
                "read_at": None,
            },
            {
                "$set": {
                    "read_at": timestamp,
                    "updated_at": timestamp,
                }
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    if updated is not None:
        return {
            "notification": updated,
            "changed": True,
        }

    existing = (
        get_notification_by_id_for_user(
            notification_object_id,
            user_object_id,
        )
    )

    return {
        "notification": existing,
        "changed": False,
    }


def mark_all_notifications_read(
    user_id,
    *,
    now=None,
):
    """Mark all currently visible unread notifications read."""

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return 0

    timestamp = (
        utc_now()
        if now is None
        else _normalize_timestamp(
            now,
            "notification read timestamp",
        )
    )

    result = (
        mongo.db.notifications.update_many(
            {
                "user_id": (
                    user_object_id
                ),
                "read_at": None,
                "dismissed_at": None,
            },
            {
                "$set": {
                    "read_at": timestamp,
                    "updated_at": timestamp,
                }
            },
        )
    )

    return result.modified_count


__all__ = [
    "DEFAULT_NOTIFICATION_PAGE_SIZE",
    "MAX_NOTIFICATION_PAGE_SIZE",
    "count_unread_notifications",
    "create_notification_idempotent",
    "get_notification_by_id_for_user",
    "list_notifications_for_user",
    "mark_all_notifications_read",
    "mark_notification_read",
]
