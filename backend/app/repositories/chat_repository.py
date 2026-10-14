"""MongoDB persistence for Xuoroni chat."""

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
from app.schemas.chat import (
    build_conversation_document,
    build_message_document,
    normalize_before_sequence,
    normalize_chat_page_size,
    normalize_client_message_id,
    normalize_message_body,
    normalize_message_sequence,
    normalize_message_type,
)


CHAT_MESSAGE_PREVIEW_LENGTH = 160


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


def _message_preview(
    body,
):
    compact = " ".join(
        str(body).split()
    )

    if (
        len(compact)
        <= CHAT_MESSAGE_PREVIEW_LENGTH
    ):
        return compact

    return (
        compact[
            :CHAT_MESSAGE_PREVIEW_LENGTH
        ]
        .rstrip()
    )


def get_conversation_by_match_id(
    match_id,
):
    match_object_id = (
        _to_object_id(
            match_id
        )
    )

    if match_object_id is None:
        return None

    return (
        mongo.db.conversations.find_one(
            {
                "match_id": (
                    match_object_id
                ),
            }
        )
    )


def get_conversation_by_id(
    conversation_id,
):
    conversation_object_id = (
        _to_object_id(
            conversation_id
        )
    )

    if conversation_object_id is None:
        return None

    return (
        mongo.db.conversations.find_one(
            {
                "_id": (
                    conversation_object_id
                ),
            }
        )
    )


def get_conversation_by_id_for_user(
    conversation_id,
    user_id,
):
    conversation_object_id = (
        _to_object_id(
            conversation_id
        )
    )

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if (
        conversation_object_id is None
        or user_object_id is None
    ):
        return None

    return (
        mongo.db.conversations.find_one(
            {
                "_id": (
                    conversation_object_id
                ),
                "member_ids": (
                    user_object_id
                ),
            }
        )
    )


def create_conversation_if_absent(
    *,
    match_id,
    first_user_id,
    second_user_id,
    now=None,
):
    document = (
        build_conversation_document(
            match_id=match_id,
            first_user_id=(
                first_user_id
            ),
            second_user_id=(
                second_user_id
            ),
            now=now,
        )
    )

    try:
        result = (
            mongo.db.conversations.insert_one(
                document
            )
        )

    except DuplicateKeyError:
        existing = (
            get_conversation_by_match_id(
                document[
                    "match_id"
                ]
            )
        )

        return {
            "conversation": existing,
            "created": False,
        }

    created = (
        mongo.db.conversations.find_one(
            {
                "_id": (
                    result.inserted_id
                ),
            }
        )
    )

    return {
        "conversation": created,
        "created": True,
    }


def list_conversations_for_user(
    user_id,
    *,
    status="active",
    limit=50,
):
    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return []

    parsed_limit = (
        normalize_chat_page_size(
            limit
        )
    )

    query = {
        "member_ids": user_object_id,
    }

    if status is not None:
        query[
            "status"
        ] = status

    return list(
        mongo.db.conversations.find(
            query
        )
        .sort(
            [
                (
                    "last_message_at",
                    DESCENDING,
                ),
                (
                    "updated_at",
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
        )
    )


def get_message_by_id(
    message_id,
):
    message_object_id = (
        _to_object_id(
            message_id
        )
    )

    if message_object_id is None:
        return None

    return mongo.db.messages.find_one(
        {
            "_id": message_object_id,
        }
    )


def get_message_by_sender_client_id(
    sender_id,
    client_message_id,
):
    sender_object_id = (
        _to_object_id(
            sender_id
        )
    )

    if sender_object_id is None:
        return None

    normalized_client_message_id = (
        normalize_client_message_id(
            client_message_id
        )
    )

    return mongo.db.messages.find_one(
        {
            "sender_id": (
                sender_object_id
            ),
            "client_message_id": (
                normalized_client_message_id
            ),
        }
    )


def _reserve_message_sequence(
    *,
    conversation_id,
    sender_id,
    recipient_id,
    now,
):
    conversation_object_id = (
        _to_object_id(
            conversation_id
        )
    )

    sender_object_id = (
        _to_object_id(
            sender_id
        )
    )

    recipient_object_id = (
        _to_object_id(
            recipient_id
        )
    )

    if (
        conversation_object_id is None
        or sender_object_id is None
        or recipient_object_id is None
    ):
        return None

    if (
        sender_object_id
        == recipient_object_id
    ):
        return None

    return (
        mongo.db.conversations.find_one_and_update(
            {
                "_id": (
                    conversation_object_id
                ),
                "status": "active",
                "member_ids": {
                    "$all": [
                        sender_object_id,
                        recipient_object_id,
                    ]
                },
            },
            {
                "$inc": {
                    "last_sequence": 1,
                },
                "$set": {
                    "updated_at": now,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )


def _increment_recipient_unread(
    *,
    conversation_id,
    recipient_id,
    now,
):
    mongo.db.conversations.update_one(
        {
            "_id": conversation_id,
        },
        {
            "$inc": {
                (
                    "member_states."
                    "$[recipient]."
                    "unread_count"
                ): 1,
            },
            "$set": {
                "updated_at": now,
            },
        },
        array_filters=[
            {
                (
                    "recipient."
                    "user_id"
                ): recipient_id,
            }
        ],
    )


def _update_latest_message_metadata(
    *,
    conversation_id,
    message_id,
    sequence,
    body,
    sent_at,
):
    mongo.db.conversations.update_one(
        {
            "_id": conversation_id,
            "$or": [
                {
                    (
                        "last_message_"
                        "sequence"
                    ): {
                        "$exists": False,
                    }
                },
                {
                    (
                        "last_message_"
                        "sequence"
                    ): {
                        "$lt": sequence,
                    }
                },
            ],
        },
        {
            "$set": {
                (
                    "last_message_"
                    "sequence"
                ): sequence,
                "last_message_id": (
                    message_id
                ),
                "last_message_at": (
                    sent_at
                ),
                "last_message_preview": (
                    _message_preview(
                        body
                    )
                ),
                "updated_at": sent_at,
            },
        },
    )


def create_message_idempotent(
    *,
    conversation_id,
    sender_id,
    recipient_id,
    client_message_id,
    body,
    message_type="text",
    now=None,
):
    conversation_object_id = (
        _to_object_id(
            conversation_id
        )
    )

    sender_object_id = (
        _to_object_id(
            sender_id
        )
    )

    recipient_object_id = (
        _to_object_id(
            recipient_id
        )
    )

    if (
        conversation_object_id is None
        or sender_object_id is None
        or recipient_object_id is None
    ):
        raise ValueError(
            "Invalid chat identifier."
        )

    if (
        sender_object_id
        == recipient_object_id
    ):
        raise ValueError(
            (
                "Message sender and "
                "recipient must be "
                "different users."
            )
        )

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

    existing = (
        get_message_by_sender_client_id(
            sender_object_id,
            normalized_client_message_id,
        )
    )

    if existing is not None:
        if (
            existing.get(
                "conversation_id"
            )
            != conversation_object_id
        ):
            raise ValueError(
                (
                    "client_message_id "
                    "was already used for "
                    "another conversation."
                )
            )

        if (
            existing.get(
                "recipient_id"
            )
            != recipient_object_id
            or existing.get(
                "body"
            )
            != normalized_body
            or existing.get(
                "message_type"
            )
            != normalized_message_type
        ):
            raise ValueError(
                (
                    "client_message_id was "
                    "already used with "
                    "different message content."
                )
            )

        return {
            "message": existing,
            "created": False,
        }

    timestamp = (
        now
        or utc_now()
    )

    conversation = (
        _reserve_message_sequence(
            conversation_id=(
                conversation_object_id
            ),
            sender_id=(
                sender_object_id
            ),
            recipient_id=(
                recipient_object_id
            ),
            now=timestamp,
        )
    )

    if conversation is None:
        return {
            "message": None,
            "created": False,
        }

    sequence = int(
        conversation.get(
            "last_sequence",
            0,
        )
    )

    if sequence < 1:
        raise RuntimeError(
            (
                "Conversation sequence "
                "allocation failed."
            )
        )

    message_document = (
        build_message_document(
            conversation_id=(
                conversation_object_id
            ),
            match_id=(
                conversation[
                    "match_id"
                ]
            ),
            sender_id=(
                sender_object_id
            ),
            recipient_id=(
                recipient_object_id
            ),
            sequence=sequence,
            client_message_id=(
                normalized_client_message_id
            ),
            body=(
                normalized_body
            ),
            message_type=(
                normalized_message_type
            ),
            now=timestamp,
        )
    )

    try:
        result = (
            mongo.db.messages.insert_one(
                message_document
            )
        )

    except DuplicateKeyError:
        existing = (
            get_message_by_sender_client_id(
                sender_object_id,
                normalized_client_message_id,
            )
        )

        if existing is None:
            raise

        if (
            existing.get(
                "conversation_id"
            )
            != conversation_object_id
        ):
            raise ValueError(
                (
                    "client_message_id "
                    "was already used for "
                    "another conversation."
                )
            ) from None

        if (
            existing.get(
                "recipient_id"
            )
            != recipient_object_id
            or existing.get(
                "body"
            )
            != normalized_body
            or existing.get(
                "message_type"
            )
            != normalized_message_type
        ):
            raise ValueError(
                (
                    "client_message_id was "
                    "already used with "
                    "different message content."
                )
            ) from None

        return {
            "message": existing,
            "created": False,
        }

    created = (
        mongo.db.messages.find_one(
            {
                "_id": (
                    result.inserted_id
                ),
            }
        )
    )

    if created is None:
        raise RuntimeError(
            (
                "Created chat message "
                "could not be reloaded."
            )
        )

    _increment_recipient_unread(
        conversation_id=(
            conversation_object_id
        ),
        recipient_id=(
            recipient_object_id
        ),
        now=timestamp,
    )

    _update_latest_message_metadata(
        conversation_id=(
            conversation_object_id
        ),
        message_id=(
            created[
                "_id"
            ]
        ),
        sequence=sequence,
        body=(
            created[
                "body"
            ]
        ),
        sent_at=(
            created[
                "sent_at"
            ]
        ),
    )

    return {
        "message": created,
        "created": True,
    }


def list_messages_for_conversation(
    conversation_id,
    user_id,
    *,
    before_sequence=None,
    limit=None,
):
    conversation = (
        get_conversation_by_id_for_user(
            conversation_id,
            user_id,
        )
    )

    if conversation is None:
        return None

    parsed_limit = (
        normalize_chat_page_size(
            limit
        )
    )

    parsed_before_sequence = (
        normalize_before_sequence(
            before_sequence
        )
    )

    query = {
        "conversation_id": (
            conversation[
                "_id"
            ]
        ),
    }

    if (
        parsed_before_sequence
        is not None
    ):
        query[
            "sequence"
        ] = {
            "$lt": (
                parsed_before_sequence
            ),
        }

    fetched = list(
        mongo.db.messages.find(
            query
        )
        .sort(
            [
                (
                    "sequence",
                    DESCENDING,
                ),
                (
                    "_id",
                    DESCENDING,
                ),
            ]
        )
        .limit(
            parsed_limit + 1
        )
    )

    has_more = (
        len(fetched)
        > parsed_limit
    )

    visible = (
        fetched[
            :parsed_limit
        ]
    )

    visible.reverse()

    next_before_sequence = None

    if (
        has_more
        and visible
    ):
        next_before_sequence = int(
            visible[
                0
            ][
                "sequence"
            ]
        )

    return {
        "conversation": (
            conversation
        ),
        "messages": visible,
        "has_more": has_more,
        "next_before_sequence": (
            next_before_sequence
        ),
    }


def _resolve_receipt_sequence(
    conversation,
    through_sequence,
):
    if through_sequence is None:
        return int(
            conversation.get(
                "last_sequence",
                0,
            )
            or 0
        )

    try:
        parsed = (
            normalize_message_sequence(
                through_sequence
            )
        )

    except ValueError:
        raise ValueError(
            (
                "Receipt sequence must "
                "be a positive integer."
            )
        ) from None

    maximum = int(
        conversation.get(
            "last_sequence",
            0,
        )
        or 0
    )

    return min(
        parsed,
        maximum,
    )


def mark_messages_delivered(
    *,
    conversation_id,
    user_id,
    through_sequence=None,
    now=None,
):
    conversation = (
        get_conversation_by_id_for_user(
            conversation_id,
            user_id,
        )
    )

    if conversation is None:
        return None

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    sequence = (
        _resolve_receipt_sequence(
            conversation,
            through_sequence,
        )
    )

    if sequence < 1:
        return {
            "conversation": conversation,
            "through_sequence": 0,
            "modified_count": 0,
        }

    timestamp = (
        now
        or utc_now()
    )

    result = (
        mongo.db.messages.update_many(
            {
                "conversation_id": (
                    conversation[
                        "_id"
                    ]
                ),
                "recipient_id": (
                    user_object_id
                ),
                "sequence": {
                    "$lte": sequence,
                },
                "delivered_at": None,
            },
            {
                "$set": {
                    "delivered_at": (
                        timestamp
                    ),
                    "updated_at": (
                        timestamp
                    ),
                },
            },
        )
    )

    mongo.db.conversations.update_one(
        {
            "_id": conversation[
                "_id"
            ],
        },
        {
            "$max": {
                (
                    "member_states."
                    "$[member]."
                    "last_delivered_sequence"
                ): sequence,
            },
            "$set": {
                "updated_at": timestamp,
            },
        },
        array_filters=[
            {
                (
                    "member."
                    "user_id"
                ): user_object_id,
            }
        ],
    )

    updated_conversation = (
        get_conversation_by_id_for_user(
            conversation[
                "_id"
            ],
            user_object_id,
        )
    )

    return {
        "conversation": (
            updated_conversation
        ),
        "through_sequence": sequence,
        "modified_count": int(
            result.modified_count
        ),
    }


def mark_messages_read(
    *,
    conversation_id,
    user_id,
    through_sequence=None,
    now=None,
):
    conversation = (
        get_conversation_by_id_for_user(
            conversation_id,
            user_id,
        )
    )

    if conversation is None:
        return None

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    sequence = (
        _resolve_receipt_sequence(
            conversation,
            through_sequence,
        )
    )

    if sequence < 1:
        return {
            "conversation": conversation,
            "through_sequence": 0,
            "modified_count": 0,
        }

    timestamp = (
        now
        or utc_now()
    )

    mongo.db.messages.update_many(
        {
            "conversation_id": (
                conversation[
                    "_id"
                ]
            ),
            "recipient_id": (
                user_object_id
            ),
            "sequence": {
                "$lte": sequence,
            },
            "delivered_at": None,
        },
        {
            "$set": {
                "delivered_at": (
                    timestamp
                ),
                "updated_at": (
                    timestamp
                ),
            },
        },
    )

    result = (
        mongo.db.messages.update_many(
            {
                "conversation_id": (
                    conversation[
                        "_id"
                    ]
                ),
                "recipient_id": (
                    user_object_id
                ),
                "sequence": {
                    "$lte": sequence,
                },
                "read_at": None,
            },
            {
                "$set": {
                    "read_at": timestamp,
                    "updated_at": (
                        timestamp
                    ),
                },
            },
        )
    )

    unread_count = (
        mongo.db.messages.count_documents(
            {
                "conversation_id": (
                    conversation[
                        "_id"
                    ]
                ),
                "recipient_id": (
                    user_object_id
                ),
                "read_at": None,
            }
        )
    )

    mongo.db.conversations.update_one(
        {
            "_id": conversation[
                "_id"
            ],
        },
        {
            "$max": {
                (
                    "member_states."
                    "$[member]."
                    "last_delivered_sequence"
                ): sequence,
                (
                    "member_states."
                    "$[member]."
                    "last_read_sequence"
                ): sequence,
            },
            "$set": {
                (
                    "member_states."
                    "$[member]."
                    "unread_count"
                ): int(
                    unread_count
                ),
                "updated_at": timestamp,
            },
        },
        array_filters=[
            {
                (
                    "member."
                    "user_id"
                ): user_object_id,
            }
        ],
    )

    updated_conversation = (
        get_conversation_by_id_for_user(
            conversation[
                "_id"
            ],
            user_object_id,
        )
    )

    return {
        "conversation": (
            updated_conversation
        ),
        "through_sequence": sequence,
        "modified_count": int(
            result.modified_count
        ),
    }


def close_conversation_by_match_id(
    match_id,
    *,
    reason="match_unavailable",
    now=None,
):
    match_object_id = (
        _to_object_id(
            match_id
        )
    )

    if match_object_id is None:
        return None

    timestamp = (
        now
        or utc_now()
    )

    updated = (
        mongo.db.conversations.find_one_and_update(
            {
                "match_id": (
                    match_object_id
                ),
                "status": "active",
            },
            {
                "$set": {
                    "status": "closed",
                    "closed_at": (
                        timestamp
                    ),
                    "closed_reason": (
                        str(reason)
                    ),
                    "updated_at": (
                        timestamp
                    ),
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    if updated is not None:
        return updated

    existing = (
        get_conversation_by_match_id(
            match_object_id
        )
    )

    if (
        existing is not None
        and existing.get(
            "status"
        )
        == "closed"
    ):
        return existing

    return None


__all__ = [
    "CHAT_MESSAGE_PREVIEW_LENGTH",
    "close_conversation_by_match_id",
    "create_conversation_if_absent",
    "create_message_idempotent",
    "get_conversation_by_id",
    "get_conversation_by_id_for_user",
    "get_conversation_by_match_id",
    "get_message_by_id",
    "get_message_by_sender_client_id",
    "list_conversations_for_user",
    "list_messages_for_conversation",
    "mark_messages_delivered",
    "mark_messages_read",
]