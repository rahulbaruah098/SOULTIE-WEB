"""Schemas and document builders for Soultie chat."""

from datetime import (
    date,
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId


CHAT_CONVERSATION_STATUSES = frozenset(
    {
        "active",
        "closed",
    }
)

CHAT_MESSAGE_TYPES = frozenset(
    {
        "text",
    }
)

CHAT_DELIVERY_STATUSES = frozenset(
    {
        "sent",
        "delivered",
        "read",
    }
)

CHAT_MODERATION_STATUSES = frozenset(
    {
        "not_reviewed",
        "allowed",
        "flagged",
        "blocked",
    }
)

CHAT_MESSAGE_MAX_LENGTH = 4000

CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH = 128

CHAT_DEFAULT_PAGE_SIZE = 30

CHAT_MAX_PAGE_SIZE = 100


def utc_now():
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
    *,
    field_name="ID",
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
        raise ValueError(
            f"Invalid {field_name}."
        ) from None


def _normalize_positive_integer(
    value,
    *,
    field_name,
    maximum=None,
):
    if isinstance(
        value,
        bool,
    ):
        raise ValueError(
            (
                f"{field_name} must be "
                "a positive integer."
            )
        )

    if isinstance(
        value,
        int,
    ):
        parsed = value

    elif isinstance(
        value,
        str,
    ):
        cleaned = value.strip()

        if not cleaned.isdigit():
            raise ValueError(
                (
                    f"{field_name} must be "
                    "a positive integer."
                )
            )

        parsed = int(
            cleaned
        )

    else:
        raise ValueError(
            (
                f"{field_name} must be "
                "a positive integer."
            )
        )

    if parsed < 1:
        raise ValueError(
            (
                f"{field_name} must be "
                "a positive integer."
            )
        )

    if (
        maximum is not None
        and parsed > maximum
    ):
        raise ValueError(
            (
                f"{field_name} must not "
                f"exceed {maximum}."
            )
        )

    return parsed


def normalize_chat_member_pair(
    first_user_id,
    second_user_id,
):
    first = _to_object_id(
        first_user_id,
        field_name="user ID",
    )

    second = _to_object_id(
        second_user_id,
        field_name="user ID",
    )

    if first == second:
        raise ValueError(
            (
                "A conversation requires "
                "two different users."
            )
        )

    ordered = sorted(
        (
            first,
            second,
        ),
        key=lambda item: str(
            item
        ),
    )

    return (
        ordered[0],
        ordered[1],
    )


def normalize_conversation_status(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            (
                "Conversation status "
                "must be text."
            )
        )

    status = (
        value.strip()
        .lower()
    )

    if (
        status
        not in CHAT_CONVERSATION_STATUSES
    ):
        raise ValueError(
            (
                "Conversation status must "
                "be active or closed."
            )
        )

    return status


def normalize_message_type(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Message type must be text."
        )

    message_type = (
        value.strip()
        .lower()
    )

    if (
        message_type
        not in CHAT_MESSAGE_TYPES
    ):
        raise ValueError(
            (
                "Unsupported chat "
                "message type."
            )
        )

    return message_type


def normalize_client_message_id(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            (
                "client_message_id "
                "must be text."
            )
        )

    client_message_id = (
        value.strip()
    )

    if not client_message_id:
        raise ValueError(
            (
                "client_message_id "
                "is required."
            )
        )

    if (
        len(
            client_message_id
        )
        > CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH
    ):
        raise ValueError(
            (
                "client_message_id must be "
                "at most "
                f"{CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH} "
                "characters."
            )
        )

    if any(
        ord(character) < 32
        for character
        in client_message_id
    ):
        raise ValueError(
            (
                "client_message_id "
                "contains invalid "
                "control characters."
            )
        )

    return client_message_id


def normalize_message_body(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Message body must be text."
        )

    body = (
        value.replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
        .strip()
    )

    if not body:
        raise ValueError(
            "Message body is required."
        )

    if (
        len(body)
        > CHAT_MESSAGE_MAX_LENGTH
    ):
        raise ValueError(
            (
                "Message body must be at "
                "most "
                f"{CHAT_MESSAGE_MAX_LENGTH} "
                "characters."
            )
        )

    invalid_control = any(
        (
            ord(character) < 32
            and character
            not in {
                "\n",
                "\t",
            }
        )
        for character in body
    )

    if invalid_control:
        raise ValueError(
            (
                "Message body contains "
                "invalid control characters."
            )
        )

    return body


def normalize_message_sequence(
    value,
):
    return _normalize_positive_integer(
        value,
        field_name="Message sequence",
    )


def normalize_chat_page_size(
    value=None,
):
    if value is None:
        return CHAT_DEFAULT_PAGE_SIZE

    return _normalize_positive_integer(
        value,
        field_name="Chat page size",
        maximum=CHAT_MAX_PAGE_SIZE,
    )


def normalize_before_sequence(
    value,
):
    if (
        value is None
        or value == ""
    ):
        return None

    return normalize_message_sequence(
        value
    )


def build_conversation_document(
    *,
    match_id,
    first_user_id,
    second_user_id,
    now=None,
):
    match_object_id = _to_object_id(
        match_id,
        field_name="match ID",
    )

    first, second = (
        normalize_chat_member_pair(
            first_user_id,
            second_user_id,
        )
    )

    timestamp = (
        now
        or utc_now()
    )

    return {
        "match_id": match_object_id,
        "member_ids": [
            first,
            second,
        ],
        "status": "active",
        "last_sequence": 0,
        "last_message_id": None,
        "last_message_at": None,
        "last_message_preview": None,
        "member_states": [
            {
                "user_id": first,
                "unread_count": 0,
                "last_read_sequence": 0,
                "last_delivered_sequence": 0,
            },
            {
                "user_id": second,
                "unread_count": 0,
                "last_read_sequence": 0,
                "last_delivered_sequence": 0,
            },
        ],
        "closed_at": None,
        "closed_reason": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_message_document(
    *,
    conversation_id,
    match_id,
    sender_id,
    recipient_id,
    sequence,
    client_message_id,
    body,
    message_type="text",
    now=None,
):
    conversation = _to_object_id(
        conversation_id,
        field_name="conversation ID",
    )

    match = _to_object_id(
        match_id,
        field_name="match ID",
    )

    sender = _to_object_id(
        sender_id,
        field_name="sender ID",
    )

    recipient = _to_object_id(
        recipient_id,
        field_name="recipient ID",
    )

    if sender == recipient:
        raise ValueError(
            (
                "Message sender and "
                "recipient must be "
                "different users."
            )
        )

    timestamp = (
        now
        or utc_now()
    )

    return {
        "conversation_id": (
            conversation
        ),
        "match_id": match,
        "sender_id": sender,
        "recipient_id": recipient,
        "sequence": (
            normalize_message_sequence(
                sequence
            )
        ),
        "client_message_id": (
            normalize_client_message_id(
                client_message_id
            )
        ),
        "message_type": (
            normalize_message_type(
                message_type
            )
        ),
        "body": (
            normalize_message_body(
                body
            )
        ),
        "sent_at": timestamp,
        "delivered_at": None,
        "read_at": None,
        "moderation_status": (
            "not_reviewed"
        ),
        "deleted_at": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def get_message_delivery_status(
    message,
):
    if not isinstance(
        message,
        dict,
    ):
        raise ValueError(
            "Message must be an object."
        )

    if (
        message.get(
            "read_at"
        )
        is not None
    ):
        return "read"

    if (
        message.get(
            "delivered_at"
        )
        is not None
    ):
        return "delivered"

    return "sent"


def _json_safe_value(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return str(
            value
        )

    if isinstance(
        value,
        datetime,
    ):
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    if isinstance(
        value,
        dict,
    ):
        return {
            key: _json_safe_value(
                item
            )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        list,
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    return value


def _member_state_for_user(
    conversation,
    user_id,
):
    current_user_id = _to_object_id(
        user_id,
        field_name="user ID",
    )

    for state in (
        conversation.get(
            "member_states"
        )
        or []
    ):
        if not isinstance(
            state,
            dict,
        ):
            continue

        try:
            state_user_id = (
                _to_object_id(
                    state.get(
                        "user_id"
                    ),
                    field_name="user ID",
                )
            )

        except ValueError:
            continue

        if (
            state_user_id
            == current_user_id
        ):
            return state

    return {
        "user_id": current_user_id,
        "unread_count": 0,
        "last_read_sequence": 0,
        "last_delivered_sequence": 0,
    }


def serialize_conversation_for_user(
    conversation,
    user_id,
):
    if not isinstance(
        conversation,
        dict,
    ):
        raise ValueError(
            (
                "Conversation must "
                "be an object."
            )
        )

    current_user_id = _to_object_id(
        user_id,
        field_name="user ID",
    )

    members = [
        _to_object_id(
            value,
            field_name="user ID",
        )
        for value in (
            conversation.get(
                "member_ids"
            )
            or []
        )
    ]

    if (
        len(members) != 2
        or current_user_id
        not in members
    ):
        raise ValueError(
            (
                "User does not belong "
                "to this conversation."
            )
        )

    other_user_id = (
        members[1]
        if members[0]
        == current_user_id
        else members[0]
    )

    state = _member_state_for_user(
        conversation,
        current_user_id,
    )

    payload = {
        "id": conversation.get(
            "_id"
        ),
        "match_id": conversation.get(
            "match_id"
        ),
        "status": conversation.get(
            "status"
        ),
        "other_user_id": (
            other_user_id
        ),
        "unread_count": int(
            state.get(
                "unread_count",
                0,
            )
            or 0
        ),
        "last_read_sequence": int(
            state.get(
                "last_read_sequence",
                0,
            )
            or 0
        ),
        "last_delivered_sequence": int(
            state.get(
                "last_delivered_sequence",
                0,
            )
            or 0
        ),
        "last_sequence": int(
            conversation.get(
                "last_sequence",
                0,
            )
            or 0
        ),
        "last_message_id": (
            conversation.get(
                "last_message_id"
            )
        ),
        "last_message_at": (
            conversation.get(
                "last_message_at"
            )
        ),
        "last_message_preview": (
            conversation.get(
                "last_message_preview"
            )
        ),
        "created_at": conversation.get(
            "created_at"
        ),
        "updated_at": conversation.get(
            "updated_at"
        ),
    }

    return _json_safe_value(
        payload
    )


def serialize_chat_message(
    message,
):
    if not isinstance(
        message,
        dict,
    ):
        raise ValueError(
            "Message must be an object."
        )

    payload = {
        "id": message.get(
            "_id"
        ),
        "conversation_id": (
            message.get(
                "conversation_id"
            )
        ),
        "match_id": message.get(
            "match_id"
        ),
        "sender_id": message.get(
            "sender_id"
        ),
        "recipient_id": (
            message.get(
                "recipient_id"
            )
        ),
        "sequence": message.get(
            "sequence"
        ),
        "client_message_id": (
            message.get(
                "client_message_id"
            )
        ),
        "message_type": (
            message.get(
                "message_type"
            )
        ),
        "body": message.get(
            "body"
        ),
        "delivery_status": (
            get_message_delivery_status(
                message
            )
        ),
        "sent_at": message.get(
            "sent_at"
        ),
        "delivered_at": (
            message.get(
                "delivered_at"
            )
        ),
        "read_at": message.get(
            "read_at"
        ),
        "moderation_status": (
            message.get(
                "moderation_status"
            )
        ),
        "deleted_at": message.get(
            "deleted_at"
        ),
    }

    return _json_safe_value(
        payload
    )


def build_message_page(
    *,
    messages,
    has_more,
    next_before_sequence=None,
):
    if not isinstance(
        messages,
        list,
    ):
        raise ValueError(
            "messages must be a list."
        )

    if not isinstance(
        has_more,
        bool,
    ):
        raise ValueError(
            "has_more must be boolean."
        )

    if (
        next_before_sequence
        is not None
    ):
        next_before_sequence = (
            normalize_message_sequence(
                next_before_sequence
            )
        )

    return {
        "messages": [
            serialize_chat_message(
                message
            )
            for message in messages
        ],
        "has_more": has_more,
        "next_before_sequence": (
            next_before_sequence
        ),
    }


__all__ = [
    "CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH",
    "CHAT_CONVERSATION_STATUSES",
    "CHAT_DEFAULT_PAGE_SIZE",
    "CHAT_DELIVERY_STATUSES",
    "CHAT_MAX_PAGE_SIZE",
    "CHAT_MESSAGE_MAX_LENGTH",
    "CHAT_MESSAGE_TYPES",
    "CHAT_MODERATION_STATUSES",
    "build_conversation_document",
    "build_message_document",
    "build_message_page",
    "get_message_delivery_status",
    "normalize_before_sequence",
    "normalize_chat_member_pair",
    "normalize_chat_page_size",
    "normalize_client_message_id",
    "normalize_conversation_status",
    "normalize_message_body",
    "normalize_message_sequence",
    "normalize_message_type",
    "serialize_chat_message",
    "serialize_conversation_for_user",
]