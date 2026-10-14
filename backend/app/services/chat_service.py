"""Business rules and access control for Xuoroni chat."""

from bson import ObjectId
from bson.errors import InvalidId

from app.repositories.chat_repository import (
    close_conversation_by_match_id,
    create_conversation_if_absent,
    create_message_idempotent,
    get_conversation_by_id_for_user,
    list_conversations_for_user,
    list_messages_for_conversation,
    mark_messages_delivered,
    mark_messages_read,
)
from app.repositories.match_repository import (
    get_match_by_id_for_user,
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
from app.schemas.chat import (
    build_message_page,
    normalize_before_sequence,
    normalize_chat_page_size,
    normalize_client_message_id,
    normalize_conversation_status,
    normalize_message_body,
    normalize_message_sequence,
    normalize_message_type,
    serialize_chat_message,
    serialize_conversation_for_user,
)


class ChatServiceError(
    RuntimeError
):
    """Base Xuoroni chat-domain error."""

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


class ChatValidationError(
    ChatServiceError
):
    pass


class ChatLifecycleError(
    ChatServiceError
):
    pass


class ChatNotFoundError(
    ChatServiceError
):
    pass


class ChatConflictError(
    ChatServiceError
):
    pass


def _to_object_id(
    value,
    *,
    field_name,
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
        raise ChatValidationError(
            "INVALID_ID",
            f"{field_name} is invalid.",
            status_code=400,
        ) from None


def _require_current_user(
    user_id,
):
    user = get_user_by_id(
        user_id
    )

    if user is None:
        raise ChatNotFoundError(
            "CURRENT_USER_NOT_FOUND",
            "Current user was not found.",
            status_code=404,
        )

    if (
        user.get(
            "account_status"
        )
        != "active"
    ):
        raise ChatLifecycleError(
            "CURRENT_ACCOUNT_INACTIVE",
            (
                "Current account is not "
                "active."
            ),
            status_code=403,
        )

    return user


def _require_current_profile(
    user_id,
):
    profile = (
        get_profile_by_user_id(
            user_id
        )
    )

    if profile is None:
        raise ChatNotFoundError(
            "CURRENT_PROFILE_NOT_FOUND",
            (
                "Current profile was "
                "not found."
            ),
            status_code=404,
        )

    if (
        profile.get(
            "onboarding_status"
        )
        != "completed"
    ):
        raise ChatLifecycleError(
            "ONBOARDING_INCOMPLETE",
            (
                "Complete onboarding "
                "before using chat."
            ),
            status_code=409,
        )

    if (
        profile.get(
            "profile_status"
        )
        != "active"
    ):
        raise ChatLifecycleError(
            "CURRENT_PROFILE_INACTIVE",
            (
                "Current profile is "
                "not active."
            ),
            status_code=403,
        )

    return profile


def _require_current_chat_eligibility(
    user_id,
):
    _require_current_user(
        user_id
    )

    _require_current_profile(
        user_id
    )


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


def _object_id_from_stored_value(
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
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                "Stored chat state is "
                "invalid."
            ),
            status_code=409,
        ) from None


def _other_user_from_pair(
    values,
    current_user_id,
    *,
    state_label,
):
    if not isinstance(
        values,
        list,
    ):
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                f"Stored {state_label} "
                "membership is invalid."
            ),
            status_code=409,
        )

    if len(values) != 2:
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                f"Stored {state_label} "
                "membership is invalid."
            ),
            status_code=409,
        )

    members = [
        _object_id_from_stored_value(
            value
        )
        for value in values
    ]

    if (
        members[
            0
        ]
        == members[
            1
        ]
        or current_user_id
        not in members
    ):
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                f"Stored {state_label} "
                "membership is invalid."
            ),
            status_code=409,
        )

    if (
        members[
            0
        ]
        == current_user_id
    ):
        return members[
            1
        ]

    return members[
        0
    ]


def _other_user_from_match(
    match,
    current_user_id,
):
    if not isinstance(
        match,
        dict,
    ):
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                "Stored match state is "
                "invalid."
            ),
            status_code=409,
        )

    return _other_user_from_pair(
        match.get(
            "user_ids"
        ),
        current_user_id,
        state_label="match",
    )


def _other_user_from_conversation(
    conversation,
    current_user_id,
):
    if not isinstance(
        conversation,
        dict,
    ):
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                "Stored conversation "
                "state is invalid."
            ),
            status_code=409,
        )

    return _other_user_from_pair(
        conversation.get(
            "member_ids"
        ),
        current_user_id,
        state_label="conversation",
    )


def _require_match_for_chat(
    *,
    match_id,
    user_id,
):
    match = (
        get_match_by_id_for_user(
            match_id,
            user_id,
        )
    )

    if match is None:
        raise ChatNotFoundError(
            "MATCH_NOT_FOUND",
            (
                "Match was not found."
            ),
            status_code=404,
        )

    if (
        match.get(
            "status"
        )
        != "active"
    ):
        raise ChatLifecycleError(
            "MATCH_INACTIVE",
            (
                "This match is no "
                "longer active."
            ),
            status_code=409,
        )

    return match


def _pair_is_available_for_chat(
    first_user_id,
    second_user_id,
):
    if pair_is_blocked(
        first_user_id,
        second_user_id,
    ):
        return False

    if not _target_is_available(
        second_user_id
    ):
        return False

    return True


def _require_pair_available_for_chat(
    first_user_id,
    second_user_id,
):
    if not _pair_is_available_for_chat(
        first_user_id,
        second_user_id,
    ):
        raise ChatLifecycleError(
            "CHAT_UNAVAILABLE",
            (
                "Chat is currently "
                "unavailable."
            ),
            status_code=403,
        )


def _require_conversation_member(
    *,
    conversation_id,
    user_id,
):
    conversation = (
        get_conversation_by_id_for_user(
            conversation_id,
            user_id,
        )
    )

    if conversation is None:
        raise ChatNotFoundError(
            "CONVERSATION_NOT_FOUND",
            (
                "Conversation was "
                "not found."
            ),
            status_code=404,
        )

    _other_user_from_conversation(
        conversation,
        user_id,
    )

    return conversation


def _close_conversation_for_inactive_match(
    conversation,
):
    match_id = conversation.get(
        "match_id"
    )

    if match_id is None:
        return

    close_conversation_by_match_id(
        match_id,
        reason="match_inactive",
    )


def _require_conversation_write_access(
    *,
    conversation_id,
    user_id,
):
    conversation = (
        _require_conversation_member(
            conversation_id=(
                conversation_id
            ),
            user_id=user_id,
        )
    )

    if (
        conversation.get(
            "status"
        )
        != "active"
    ):
        raise ChatLifecycleError(
            "CONVERSATION_CLOSED",
            (
                "This conversation "
                "is closed."
            ),
            status_code=409,
        )

    other_user_id = (
        _other_user_from_conversation(
            conversation,
            user_id,
        )
    )

    match_id = conversation.get(
        "match_id"
    )

    if match_id is None:
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                "Conversation is not "
                "linked to a match."
            ),
            status_code=409,
        )

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
        _close_conversation_for_inactive_match(
            conversation
        )

        raise ChatLifecycleError(
            "MATCH_INACTIVE",
            (
                "This match is no "
                "longer active."
            ),
            status_code=409,
        )

    match_other_user_id = (
        _other_user_from_match(
            match,
            user_id,
        )
    )

    if (
        match_other_user_id
        != other_user_id
    ):
        raise ChatConflictError(
            "CHAT_STATE_INVALID",
            (
                "Conversation and match "
                "membership do not agree."
            ),
            status_code=409,
        )

    _require_pair_available_for_chat(
        user_id,
        other_user_id,
    )

    return (
        conversation,
        other_user_id,
        match,
    )


def ensure_chat_for_match(
    *,
    user_id,
    match_id,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    match_object_id = (
        _to_object_id(
            match_id,
            field_name="match_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    match = _require_match_for_chat(
        match_id=match_object_id,
        user_id=user,
    )

    other_user_id = (
        _other_user_from_match(
            match,
            user,
        )
    )

    _require_pair_available_for_chat(
        user,
        other_user_id,
    )

    result = (
        create_conversation_if_absent(
            match_id=match[
                "_id"
            ],
            first_user_id=user,
            second_user_id=(
                other_user_id
            ),
        )
    )

    conversation = result.get(
        "conversation"
    )

    if conversation is None:
        raise ChatConflictError(
            "CONVERSATION_CREATION_FAILED",
            (
                "Conversation could "
                "not be created."
            ),
            status_code=409,
        )

    return {
        "created": bool(
            result.get(
                "created"
            )
        ),
        "conversation": (
            serialize_conversation_for_user(
                conversation,
                user,
            )
        ),
    }


def list_my_conversations(
    user_id,
    *,
    status="active",
    limit=50,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    _require_current_chat_eligibility(
        user
    )

    try:
        normalized_status = (
            normalize_conversation_status(
                status
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_CONVERSATION_STATUS",
            str(
                exc
            ),
            status_code=400,
        ) from None

    try:
        parsed_limit = (
            normalize_chat_page_size(
                limit
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_LIMIT",
            str(
                exc
            ),
            status_code=400,
        ) from None

    conversations = (
        list_conversations_for_user(
            user,
            status=normalized_status,
            limit=parsed_limit,
        )
    )

    return {
        "conversations": [
            serialize_conversation_for_user(
                conversation,
                user,
            )
            for conversation
            in conversations
        ],
        "returned": len(
            conversations
        ),
    }


def get_my_conversation(
    *,
    user_id,
    conversation_id,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    conversation_object_id = (
        _to_object_id(
            conversation_id,
            field_name="conversation_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    conversation = (
        _require_conversation_member(
            conversation_id=(
                conversation_object_id
            ),
            user_id=user,
        )
    )

    return (
        serialize_conversation_for_user(
            conversation,
            user,
        )
    )


def list_my_messages(
    *,
    user_id,
    conversation_id,
    before_sequence=None,
    limit=None,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    conversation_object_id = (
        _to_object_id(
            conversation_id,
            field_name="conversation_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    conversation = (
        _require_conversation_member(
            conversation_id=(
                conversation_object_id
            ),
            user_id=user,
        )
    )

    try:
        parsed_limit = (
            normalize_chat_page_size(
                limit
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_LIMIT",
            str(
                exc
            ),
            status_code=400,
        ) from None

    try:
        parsed_before_sequence = (
            normalize_before_sequence(
                before_sequence
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_MESSAGE_CURSOR",
            str(
                exc
            ),
            status_code=400,
        ) from None

    result = (
        list_messages_for_conversation(
            conversation[
                "_id"
            ],
            user,
            before_sequence=(
                parsed_before_sequence
            ),
            limit=parsed_limit,
        )
    )

    if result is None:
        raise ChatNotFoundError(
            "CONVERSATION_NOT_FOUND",
            (
                "Conversation was "
                "not found."
            ),
            status_code=404,
        )

    page = build_message_page(
        messages=result[
            "messages"
        ],
        has_more=result[
            "has_more"
        ],
        next_before_sequence=(
            result[
                "next_before_sequence"
            ]
        ),
    )

    return {
        "conversation": (
            serialize_conversation_for_user(
                conversation,
                user,
            )
        ),
        **page,
    }


def send_chat_message(
    *,
    user_id,
    conversation_id,
    client_message_id,
    body,
    message_type="text",
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    conversation_object_id = (
        _to_object_id(
            conversation_id,
            field_name="conversation_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    try:
        normalized_client_message_id = (
            normalize_client_message_id(
                client_message_id
            )
        )

        normalized_body = (
            normalize_message_body(
                body
            )
        )

        normalized_message_type = (
            normalize_message_type(
                message_type
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_MESSAGE",
            str(
                exc
            ),
            status_code=400,
        ) from None

    (
        conversation,
        recipient_id,
        _match,
    ) = (
        _require_conversation_write_access(
            conversation_id=(
                conversation_object_id
            ),
            user_id=user,
        )
    )

    try:
        result = (
            create_message_idempotent(
                conversation_id=(
                    conversation[
                        "_id"
                    ]
                ),
                sender_id=user,
                recipient_id=(
                    recipient_id
                ),
                client_message_id=(
                    normalized_client_message_id
                ),
                body=normalized_body,
                message_type=(
                    normalized_message_type
                ),
            )
        )

    except ValueError as exc:
        message = str(
            exc
        )

        if (
            "client_message_id"
            in message
            and (
                "already used"
                in message
            )
        ):
            raise ChatConflictError(
                "MESSAGE_IDEMPOTENCY_CONFLICT",
                (
                    "client_message_id "
                    "was already used for "
                    "a different message."
                ),
                status_code=409,
            ) from None

        raise ChatConflictError(
            "MESSAGE_CREATION_CONFLICT",
            (
                "Message could not "
                "be created."
            ),
            status_code=409,
        ) from None

    message = result.get(
        "message"
    )

    if message is None:
        raise ChatLifecycleError(
            "CONVERSATION_CLOSED",
            (
                "This conversation "
                "is closed."
            ),
            status_code=409,
        )

    return {
        "created": bool(
            result.get(
                "created"
            )
        ),
        "message": (
            serialize_chat_message(
                message
            )
        ),
    }


def mark_chat_delivered(
    *,
    user_id,
    conversation_id,
    through_sequence=None,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    conversation_object_id = (
        _to_object_id(
            conversation_id,
            field_name="conversation_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    if (
        through_sequence
        is not None
    ):
        try:
            through_sequence = (
                normalize_message_sequence(
                    through_sequence
                )
            )

        except ValueError as exc:
            raise ChatValidationError(
                "INVALID_RECEIPT_SEQUENCE",
                str(
                    exc
                ),
                status_code=400,
            ) from None

    (
        conversation,
        _other_user_id,
        _match,
    ) = (
        _require_conversation_write_access(
            conversation_id=(
                conversation_object_id
            ),
            user_id=user,
        )
    )

    try:
        result = (
            mark_messages_delivered(
                conversation_id=(
                    conversation[
                        "_id"
                    ]
                ),
                user_id=user,
                through_sequence=(
                    through_sequence
                ),
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_RECEIPT_SEQUENCE",
            str(
                exc
            ),
            status_code=400,
        ) from None

    if result is None:
        raise ChatNotFoundError(
            "CONVERSATION_NOT_FOUND",
            (
                "Conversation was "
                "not found."
            ),
            status_code=404,
        )

    return {
        "conversation": (
            serialize_conversation_for_user(
                result[
                    "conversation"
                ],
                user,
            )
        ),
        "through_sequence": (
            result[
                "through_sequence"
            ]
        ),
        "modified_count": (
            result[
                "modified_count"
            ]
        ),
    }


def mark_chat_read(
    *,
    user_id,
    conversation_id,
    through_sequence=None,
):
    user = _to_object_id(
        user_id,
        field_name="user_id",
    )

    conversation_object_id = (
        _to_object_id(
            conversation_id,
            field_name="conversation_id",
        )
    )

    _require_current_chat_eligibility(
        user
    )

    if (
        through_sequence
        is not None
    ):
        try:
            through_sequence = (
                normalize_message_sequence(
                    through_sequence
                )
            )

        except ValueError as exc:
            raise ChatValidationError(
                "INVALID_RECEIPT_SEQUENCE",
                str(
                    exc
                ),
                status_code=400,
            ) from None

    (
        conversation,
        _other_user_id,
        _match,
    ) = (
        _require_conversation_write_access(
            conversation_id=(
                conversation_object_id
            ),
            user_id=user,
        )
    )

    try:
        result = (
            mark_messages_read(
                conversation_id=(
                    conversation[
                        "_id"
                    ]
                ),
                user_id=user,
                through_sequence=(
                    through_sequence
                ),
            )
        )

    except ValueError as exc:
        raise ChatValidationError(
            "INVALID_RECEIPT_SEQUENCE",
            str(
                exc
            ),
            status_code=400,
        ) from None

    if result is None:
        raise ChatNotFoundError(
            "CONVERSATION_NOT_FOUND",
            (
                "Conversation was "
                "not found."
            ),
            status_code=404,
        )

    return {
        "conversation": (
            serialize_conversation_for_user(
                result[
                    "conversation"
                ],
                user,
            )
        ),
        "through_sequence": (
            result[
                "through_sequence"
            ]
        ),
        "modified_count": (
            result[
                "modified_count"
            ]
        ),
    }


def close_chat_for_match(
    *,
    match_id,
    reason="match_unavailable",
):
    match_object_id = (
        _to_object_id(
            match_id,
            field_name="match_id",
        )
    )

    updated = (
        close_conversation_by_match_id(
            match_object_id,
            reason=reason,
        )
    )

    if updated is None:
        return None

    return {
        "conversation_id": str(
            updated[
                "_id"
            ]
        ),
        "match_id": str(
            updated[
                "match_id"
            ]
        ),
        "status": updated.get(
            "status"
        ),
        "closed_reason": (
            updated.get(
                "closed_reason"
            )
        ),
    }


__all__ = [
    "ChatConflictError",
    "ChatLifecycleError",
    "ChatNotFoundError",
    "ChatServiceError",
    "ChatValidationError",
    "close_chat_for_match",
    "ensure_chat_for_match",
    "get_my_conversation",
    "list_my_conversations",
    "list_my_messages",
    "mark_chat_delivered",
    "mark_chat_read",
    "send_chat_message",
]