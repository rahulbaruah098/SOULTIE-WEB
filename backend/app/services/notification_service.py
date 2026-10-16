"""Privacy-safe Notification business logic for Xuoroni."""

import base64
import binascii
import json
import logging
from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId

from app.realtime.notifications import (
    emit_notification_created,
)
from app.repositories.chat_repository import (
    get_conversation_by_id_for_user,
)
from app.repositories.match_repository import (
    get_match_by_id_for_user,
)
from app.repositories.notification_preferences_repository import (
    ensure_notification_preferences_for_user,
    get_notification_preferences_for_user,
    update_notification_preferences_for_user,
)
from app.repositories.notification_repository import (
    DEFAULT_NOTIFICATION_PAGE_SIZE,
    MAX_NOTIFICATION_PAGE_SIZE,
    create_notification_idempotent,
    get_notification_by_id_for_user,
    list_notifications_for_user,
    mark_notification_read,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
from app.repositories.safety_repository import (
    pair_is_blocked,
)
from app.repositories.user_repository import (
    get_user_by_id,
)
from app.schemas.notification_preferences import (
    normalize_notification_preferences_patch,
    notification_type_is_enabled,
    serialize_notification_preferences_for_owner,
)
from app.schemas.notification import (
    normalize_notification_resource,
    serialize_notification_for_user,
)


NOTIFICATION_CURSOR_MAX_LENGTH = 512

UNAVAILABLE_ACCOUNT_STATUSES = frozenset(
    {
        "suspended",
        "banned",
        "deleted",
    }
)


logger = logging.getLogger(
    __name__
)


class NotificationServiceError(
    RuntimeError
):
    """Base Xuoroni Notification-domain error."""

    def __init__(
        self,
        code,
        message,
        *,
        status_code=400,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message
        self.status_code = status_code


class NotificationValidationError(
    NotificationServiceError
):
    pass


class NotificationLifecycleError(
    NotificationServiceError
):
    pass


class NotificationNotFoundError(
    NotificationServiceError
):
    pass


class NotificationConflictError(
    NotificationServiceError
):
    pass


def _object_id_or_none(
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


def _to_object_id(
    value,
    *,
    field_name,
):
    object_id = (
        _object_id_or_none(
            value
        )
    )

    if object_id is None:
        raise NotificationValidationError(
            "INVALID_ID",
            f"{field_name} is invalid.",
            status_code=400,
        )

    return object_id


def _utc_timestamp(
    value,
    *,
    field_name,
):
    if not isinstance(
        value,
        datetime,
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            f"{field_name} is invalid.",
            status_code=400,
        )

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _require_current_user(
    user_id,
):
    user = get_user_by_id(
        user_id
    )

    if user is None:
        raise NotificationNotFoundError(
            "CURRENT_USER_NOT_FOUND",
            "Current user was not found.",
            status_code=404,
        )

    if (
        user.get(
            "account_status"
        )
        in UNAVAILABLE_ACCOUNT_STATUSES
    ):
        raise NotificationLifecycleError(
            "CURRENT_ACCOUNT_UNAVAILABLE",
            (
                "Current account is "
                "unavailable."
            ),
            status_code=403,
        )

    return user


def _target_is_available(
    user_id,
):
    user = get_user_by_id(
        user_id
    )

    if (
        user is None
        or user.get(
            "account_status"
        )
        != "active"
    ):
        return False

    profile = (
        get_profile_by_user_id(
            user_id
        )
    )

    if (
        profile is None
        or profile.get(
            "onboarding_status"
        )
        != "completed"
        or profile.get(
            "profile_status"
        )
        != "active"
    ):
        return False

    return True


def _peer_from_pair(
    values,
    current_user_id,
):
    if (
        not isinstance(
            values,
            list,
        )
        or len(values) != 2
    ):
        return None

    members = [
        _object_id_or_none(
            value
        )
        for value in values
    ]

    if (
        members[0] is None
        or members[1] is None
        or members[0] == members[1]
        or current_user_id not in members
    ):
        return None

    if members[0] == current_user_id:
        return members[1]

    return members[0]


def _match_resource_is_visible(
    resource_id,
    user_id,
):
    match = (
        get_match_by_id_for_user(
            resource_id,
            user_id,
        )
    )

    if (
        match is None
        or match.get(
            "status"
        )
        != "active"
    ):
        return False

    peer_id = _peer_from_pair(
        match.get(
            "user_ids"
        ),
        user_id,
    )

    if peer_id is None:
        return False

    if pair_is_blocked(
        user_id,
        peer_id,
    ):
        return False

    return _target_is_available(
        peer_id
    )


def _conversation_resource_is_visible(
    resource_id,
    user_id,
):
    conversation = (
        get_conversation_by_id_for_user(
            resource_id,
            user_id,
        )
    )

    if (
        conversation is None
        or conversation.get(
            "status"
        )
        != "active"
    ):
        return False

    conversation_peer = (
        _peer_from_pair(
            conversation.get(
                "member_ids"
            ),
            user_id,
        )
    )

    if conversation_peer is None:
        return False

    match_id = (
        _object_id_or_none(
            conversation.get(
                "match_id"
            )
        )
    )

    if match_id is None:
        return False

    match = (
        get_match_by_id_for_user(
            match_id,
            user_id,
        )
    )

    if (
        match is None
        or match.get(
            "status"
        )
        != "active"
    ):
        return False

    match_peer = _peer_from_pair(
        match.get(
            "user_ids"
        ),
        user_id,
    )

    if (
        match_peer is None
        or match_peer
        != conversation_peer
    ):
        return False

    if pair_is_blocked(
        user_id,
        conversation_peer,
    ):
        return False

    return _target_is_available(
        conversation_peer
    )


def _resource_is_visible(
    notification,
    user_id,
):
    resource_type = (
        notification.get(
            "resource_type"
        )
    )

    resource_id = (
        notification.get(
            "resource_id"
        )
    )

    if resource_type is None:
        return (
            resource_id
            is None
        )

    if resource_type == "account":
        return (
            _object_id_or_none(
                resource_id
            )
            == user_id
        )

    if resource_type == "match":
        return (
            _match_resource_is_visible(
                resource_id,
                user_id,
            )
        )

    if resource_type == "conversation":
        return (
            _conversation_resource_is_visible(
                resource_id,
                user_id,
            )
        )

    # Remaining resource domains are introduced in later
    # milestones. Until their own authorization contracts
    # exist, resource-linked notifications fail closed.
    return False


def _serialize_visible_notification(
    notification,
    user_id,
):
    try:
        payload = (
            serialize_notification_for_user(
                notification,
                user_id,
            )
        )

    except ValueError:
        # Malformed durable data must never leak through
        # the Notification Center.
        return None

    if not _resource_is_visible(
        notification,
        user_id,
    ):
        return None

    return payload


def encode_notification_cursor(
    *,
    created_at,
    notification_id,
):
    timestamp = _utc_timestamp(
        created_at,
        field_name=(
            "Notification cursor timestamp"
        ),
    )

    object_id = _to_object_id(
        notification_id,
        field_name=(
            "Notification cursor ID"
        ),
    )

    payload = {
        "created_at": (
            timestamp.isoformat()
        ),
        "notification_id": str(
            object_id
        ),
    }

    raw = json.dumps(
        payload,
        separators=(
            ",",
            ":",
        ),
        sort_keys=True,
    ).encode(
        "utf-8"
    )

    return (
        base64.urlsafe_b64encode(
            raw
        )
        .decode(
            "ascii"
        )
        .rstrip(
            "="
        )
    )


def decode_notification_cursor(
    value,
):
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Notification cursor must be text.",
            status_code=400,
        )

    cleaned = value.strip()

    if not cleaned:
        return None

    if (
        len(cleaned)
        > NOTIFICATION_CURSOR_MAX_LENGTH
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Notification cursor is too long.",
            status_code=400,
        )

    padding = (
        "="
        * (
            -len(cleaned)
            % 4
        )
    )

    try:
        raw = (
            base64.urlsafe_b64decode(
                cleaned
                + padding
            )
        )

        payload = json.loads(
            raw.decode(
                "utf-8"
            )
        )

    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Invalid Notification cursor.",
            status_code=400,
        ) from None

    if (
        not isinstance(
            payload,
            dict,
        )
        or set(
            payload
        )
        != {
            "created_at",
            "notification_id",
        }
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Invalid Notification cursor.",
            status_code=400,
        )

    raw_timestamp = (
        payload.get(
            "created_at"
        )
    )

    if not isinstance(
        raw_timestamp,
        str,
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Invalid Notification cursor.",
            status_code=400,
        )

    try:
        timestamp = (
            datetime.fromisoformat(
                raw_timestamp
            )
        )

    except ValueError:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Invalid Notification cursor.",
            status_code=400,
        ) from None

    timestamp = _utc_timestamp(
        timestamp,
        field_name=(
            "Notification cursor timestamp"
        ),
    )

    notification_id = (
        _object_id_or_none(
            payload.get(
                "notification_id"
            )
        )
    )

    if notification_id is None:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_CURSOR",
            "Invalid Notification cursor.",
            status_code=400,
        )

    return {
        "created_at": timestamp,
        "notification_id": (
            notification_id
        ),
    }


def _cursor_from_document(
    notification,
):
    if not isinstance(
        notification,
        dict,
    ):
        raise NotificationConflictError(
            "NOTIFICATION_STATE_INVALID",
            "Stored Notification state is invalid.",
            status_code=409,
        )

    created_at = (
        notification.get(
            "created_at"
        )
    )

    notification_id = (
        _object_id_or_none(
            notification.get(
                "_id"
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
        raise NotificationConflictError(
            "NOTIFICATION_STATE_INVALID",
            "Stored Notification state is invalid.",
            status_code=409,
        )

    if created_at.tzinfo is None:
        created_at = (
            created_at.replace(
                tzinfo=timezone.utc
            )
        )

    return {
        "created_at": (
            created_at.astimezone(
                timezone.utc
            )
        ),
        "notification_id": (
            notification_id
        ),
    }


def _emit_notification_created_best_effort(
    *,
    user_id,
    notification,
):
    """
    Signal a newly-created durable Notification over realtime.

    Durable MongoDB state remains authoritative. Realtime delivery
    is supplementary and must never invalidate successful durable
    Notification creation.
    """

    try:
        emit_notification_created(
            user_id=user_id,
            notification=notification,
        )

    except Exception:
        logger.warning(
            (
                "Realtime notification.created delivery "
                "failed after durable Notification creation."
            ),
            exc_info=True,
        )


def create_notification(
    *,
    user_id,
    notification_type,
    resource_type=None,
    resource_id=None,
    dedupe_key=None,
    now=None,
):
    """Create one recipient notification through the service boundary."""

    recipient = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        recipient
    )

    try:
        (
            normalized_resource_type,
            normalized_resource_id,
        ) = normalize_notification_resource(
            resource_type,
            resource_id,
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_RESOURCE",
            str(
                exc
            ),
            status_code=400,
        ) from None

    candidate = {
        "resource_type": (
            normalized_resource_type
        ),
        "resource_id": (
            normalized_resource_id
        ),
    }

    if not _resource_is_visible(
        candidate,
        recipient,
    ):
        raise NotificationLifecycleError(
            "NOTIFICATION_RESOURCE_UNAVAILABLE",
            (
                "Notification resource "
                "is unavailable."
            ),
            status_code=409,
        )

    # User-disableable Notification types are suppressed before durable write.
    # Mandatory safety/security types always remain enabled by schema contract.
    if not notification_type_is_enabled_for_user(
        recipient,
        notification_type,
    ):
        return {
            "notification": None,
            "created": False,
        }

    try:
        result = (
            create_notification_idempotent(
                user_id=recipient,
                notification_type=(
                    notification_type
                ),
                resource_type=(
                    normalized_resource_type
                ),
                resource_id=(
                    normalized_resource_id
                ),
                dedupe_key=dedupe_key,
                now=now,
            )
        )

    except ValueError as exc:
        message = str(
            exc
        )

        if (
            "dedupe key was already used"
            in message
        ):
            raise NotificationConflictError(
                "NOTIFICATION_DEDUPE_CONFLICT",
                (
                    "Notification dedupe key "
                    "conflicts with an existing "
                    "notification."
                ),
                status_code=409,
            ) from None

        raise NotificationValidationError(
            "INVALID_NOTIFICATION",
            message,
            status_code=400,
        ) from None

    notification = result.get(
        "notification"
    )

    payload = (
        _serialize_visible_notification(
            notification,
            recipient,
        )
    )

    if payload is None:
        raise NotificationConflictError(
            "NOTIFICATION_STATE_INVALID",
            (
                "Notification could not "
                "be safely serialized."
            ),
            status_code=409,
        )

    if bool(
        result.get(
            "created"
        )
    ):
        _emit_notification_created_best_effort(
            user_id=recipient,
            notification=payload,
        )

    return {
        "created": bool(
            result.get(
                "created"
            )
        ),
        "notification": payload,
    }


def list_my_notifications(
    user_id,
    *,
    limit=DEFAULT_NOTIFICATION_PAGE_SIZE,
    cursor=None,
    unread_only=False,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        user
    )

    decoded_cursor = (
        decode_notification_cursor(
            cursor
        )
    )

    try:
        result = (
            list_notifications_for_user(
                user,
                limit=limit,
                cursor=decoded_cursor,
                unread_only=unread_only,
                include_dismissed=False,
            )
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_LIST_QUERY",
            str(
                exc
            ),
            status_code=400,
        ) from None

    raw_notifications = (
        result.get(
            "notifications"
        )
    )

    if not isinstance(
        raw_notifications,
        list,
    ):
        raise NotificationConflictError(
            "NOTIFICATION_STATE_INVALID",
            "Stored Notification state is invalid.",
            status_code=409,
        )

    notifications = []

    for notification in raw_notifications:
        payload = (
            _serialize_visible_notification(
                notification,
                user,
            )
        )

        if payload is not None:
            notifications.append(
                payload
            )

    has_more = bool(
        result.get(
            "has_more"
        )
    )

    next_cursor = None

    if (
        has_more
        and raw_notifications
    ):
        last_cursor = (
            _cursor_from_document(
                raw_notifications[-1]
            )
        )

        next_cursor = (
            encode_notification_cursor(
                created_at=(
                    last_cursor[
                        "created_at"
                    ]
                ),
                notification_id=(
                    last_cursor[
                        "notification_id"
                    ]
                ),
            )
        )

    return {
        "notifications": notifications,
        "returned": len(
            notifications
        ),
        "has_more": has_more,
        "next_cursor": (
            next_cursor
        ),
    }


def get_my_unread_notification_count(
    user_id,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        user
    )

    cursor = None
    count = 0

    while True:
        result = (
            list_notifications_for_user(
                user,
                limit=(
                    MAX_NOTIFICATION_PAGE_SIZE
                ),
                cursor=cursor,
                unread_only=True,
                include_dismissed=False,
            )
        )

        raw_notifications = (
            result.get(
                "notifications"
            )
        )

        if not isinstance(
            raw_notifications,
            list,
        ):
            raise NotificationConflictError(
                "NOTIFICATION_STATE_INVALID",
                (
                    "Stored Notification "
                    "state is invalid."
                ),
                status_code=409,
            )

        for notification in raw_notifications:
            if (
                _serialize_visible_notification(
                    notification,
                    user,
                )
                is not None
            ):
                count += 1

        if not result.get(
            "has_more"
        ):
            break

        if not raw_notifications:
            raise NotificationConflictError(
                "NOTIFICATION_STATE_INVALID",
                (
                    "Notification pagination "
                    "state is invalid."
                ),
                status_code=409,
            )

        cursor = (
            _cursor_from_document(
                raw_notifications[-1]
            )
        )

    return {
        "unread_count": count,
    }


def mark_my_notification_read(
    *,
    user_id,
    notification_id,
    now=None,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    notification_object_id = (
        _to_object_id(
            notification_id,
            field_name="notification_id",
        )
    )

    _require_current_user(
        user
    )

    existing = (
        get_notification_by_id_for_user(
            notification_object_id,
            user,
        )
    )

    if (
        existing is None
        or _serialize_visible_notification(
            existing,
            user,
        )
        is None
    ):
        raise NotificationNotFoundError(
            "NOTIFICATION_NOT_FOUND",
            "Notification was not found.",
            status_code=404,
        )

    try:
        result = (
            mark_notification_read(
                notification_object_id,
                user,
                now=now,
            )
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_READ_TIME",
            str(
                exc
            ),
            status_code=400,
        ) from None

    notification = result.get(
        "notification"
    )

    payload = (
        _serialize_visible_notification(
            notification,
            user,
        )
    )

    if payload is None:
        raise NotificationNotFoundError(
            "NOTIFICATION_NOT_FOUND",
            "Notification was not found.",
            status_code=404,
        )

    return {
        "changed": bool(
            result.get(
                "changed"
            )
        ),
        "notification": payload,
    }


def mark_all_my_notifications_read(
    user_id,
    *,
    now=None,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        user
    )

    cursor = None
    changed = 0

    while True:
        result = (
            list_notifications_for_user(
                user,
                limit=(
                    MAX_NOTIFICATION_PAGE_SIZE
                ),
                cursor=cursor,
                unread_only=True,
                include_dismissed=False,
            )
        )

        raw_notifications = (
            result.get(
                "notifications"
            )
        )

        if not isinstance(
            raw_notifications,
            list,
        ):
            raise NotificationConflictError(
                "NOTIFICATION_STATE_INVALID",
                (
                    "Stored Notification "
                    "state is invalid."
                ),
                status_code=409,
            )

        for notification in raw_notifications:
            payload = (
                _serialize_visible_notification(
                    notification,
                    user,
                )
            )

            if payload is None:
                continue

            update = (
                mark_notification_read(
                    notification.get(
                        "_id"
                    ),
                    user,
                    now=now,
                )
            )

            if update.get(
                "changed"
            ):
                changed += 1

        if not result.get(
            "has_more"
        ):
            break

        if not raw_notifications:
            raise NotificationConflictError(
                "NOTIFICATION_STATE_INVALID",
                (
                    "Notification pagination "
                    "state is invalid."
                ),
                status_code=409,
            )

        cursor = (
            _cursor_from_document(
                raw_notifications[-1]
            )
        )

    return {
        "changed": changed,
    }


def get_my_notification_preferences(
    user_id,
):
    """Return the authenticated user's durable in-app preferences."""

    owner_id = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        owner_id
    )

    preferences = (
        ensure_notification_preferences_for_user(
            owner_id
        )
    )

    if preferences is None:
        raise NotificationLifecycleError(
            "NOTIFICATION_PREFERENCES_UNAVAILABLE",
            (
                "Notification preferences "
                "are unavailable."
            ),
            status_code=409,
        )

    return {
        "preferences": (
            serialize_notification_preferences_for_owner(
                preferences
            )
        ),
    }


def update_my_notification_preferences(
    user_id,
    payload,
    *,
    now=None,
):
    """Validate and persist the authenticated user's in-app preferences."""

    owner_id = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        owner_id
    )

    try:
        patch = (
            normalize_notification_preferences_patch(
                payload
            )
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_PREFERENCES",
            str(
                exc
            ),
            status_code=400,
        ) from None

    try:
        preferences = (
            update_notification_preferences_for_user(
                owner_id,
                disabled_types=(
                    patch[
                        "disabled_types"
                    ]
                ),
                now=now,
            )
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_PREFERENCES",
            str(
                exc
            ),
            status_code=400,
        ) from None

    if preferences is None:
        raise NotificationLifecycleError(
            "NOTIFICATION_PREFERENCES_UNAVAILABLE",
            (
                "Notification preferences "
                "are unavailable."
            ),
            status_code=409,
        )

    return {
        "preferences": (
            serialize_notification_preferences_for_owner(
                preferences
            )
        ),
    }


def notification_type_is_enabled_for_user(
    user_id,
    notification_type,
):
    """Return whether one in-app Notification type is enabled for a user."""

    owner_id = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_user(
        owner_id
    )

    preferences = (
        get_notification_preferences_for_user(
            owner_id
        )
    )

    try:
        return notification_type_is_enabled(
            preferences,
            notification_type,
        )

    except ValueError as exc:
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_TYPE",
            str(
                exc
            ),
            status_code=400,
        ) from None

__all__ = [
    "NOTIFICATION_CURSOR_MAX_LENGTH",
    "NotificationConflictError",
    "NotificationLifecycleError",
    "NotificationNotFoundError",
    "NotificationServiceError",
    "NotificationValidationError",
    "create_notification",
    "decode_notification_cursor",
    "encode_notification_cursor",
    "get_my_notification_preferences",
    "get_my_unread_notification_count",
    "list_my_notifications",
    "mark_all_my_notifications_read",
    "mark_my_notification_read",
    "notification_type_is_enabled_for_user",
    "update_my_notification_preferences",
]
