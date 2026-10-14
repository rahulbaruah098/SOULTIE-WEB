"""Tests for Xuoroni chat schemas."""

from datetime import (
    datetime,
    timezone,
)

import pytest
from bson import ObjectId

from app.schemas.chat import (
    CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH,
    CHAT_CONVERSATION_STATUSES,
    CHAT_DEFAULT_PAGE_SIZE,
    CHAT_DELIVERY_STATUSES,
    CHAT_MAX_PAGE_SIZE,
    CHAT_MESSAGE_MAX_LENGTH,
    CHAT_MESSAGE_TYPES,
    CHAT_MODERATION_STATUSES,
    build_conversation_document,
    build_message_document,
    build_message_page,
    get_message_delivery_status,
    normalize_before_sequence,
    normalize_chat_member_pair,
    normalize_chat_page_size,
    normalize_client_message_id,
    normalize_conversation_status,
    normalize_message_body,
    normalize_message_sequence,
    normalize_message_type,
    serialize_chat_message,
    serialize_conversation_for_user,
)


FIXED_TIME = datetime(
    2026,
    10,
    13,
    18,
    0,
    tzinfo=timezone.utc,
)


def test_chat_constants_are_locked():
    assert (
        CHAT_CONVERSATION_STATUSES
        == frozenset(
            {
                "active",
                "closed",
            }
        )
    )

    assert (
        CHAT_MESSAGE_TYPES
        == frozenset(
            {
                "text",
            }
        )
    )

    assert (
        CHAT_DELIVERY_STATUSES
        == frozenset(
            {
                "sent",
                "delivered",
                "read",
            }
        )
    )

    assert (
        CHAT_MODERATION_STATUSES
        == frozenset(
            {
                "not_reviewed",
                "allowed",
                "flagged",
                "blocked",
            }
        )
    )

    assert CHAT_MESSAGE_MAX_LENGTH == 4000
    assert CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH == 128
    assert CHAT_DEFAULT_PAGE_SIZE == 30
    assert CHAT_MAX_PAGE_SIZE == 100


def test_member_pair_is_canonical():
    first = ObjectId(
        "000000000000000000000002"
    )

    second = ObjectId(
        "000000000000000000000001"
    )

    result = normalize_chat_member_pair(
        first,
        second,
    )

    assert result == (
        second,
        first,
    )


def test_member_pair_accepts_string_ids():
    first = ObjectId()
    second = ObjectId()

    result = normalize_chat_member_pair(
        str(first),
        str(second),
    )

    assert set(result) == {
        first,
        second,
    }


def test_member_pair_rejects_same_user():
    user_id = ObjectId()

    with pytest.raises(
        ValueError,
        match=(
            "two different users"
        ),
    ):
        normalize_chat_member_pair(
            user_id,
            user_id,
        )


def test_member_pair_rejects_invalid_id():
    with pytest.raises(
        ValueError,
        match="Invalid user ID",
    ):
        normalize_chat_member_pair(
            "not-an-object-id",
            ObjectId(),
        )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (
            "active",
            "active",
        ),
        (
            " ACTIVE ",
            "active",
        ),
        (
            "Closed",
            "closed",
        ),
    ],
)
def test_conversation_status_normalization(
    value,
    expected,
):
    assert (
        normalize_conversation_status(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        "paused",
        "",
        12,
        None,
    ],
)
def test_invalid_conversation_status_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_conversation_status(
            value
        )


def test_message_type_normalization():
    assert (
        normalize_message_type(
            " TEXT "
        )
        == "text"
    )


@pytest.mark.parametrize(
    "value",
    [
        "image",
        "video",
        "",
        None,
    ],
)
def test_unsupported_message_type_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_message_type(
            value
        )


def test_client_message_id_is_trimmed():
    assert (
        normalize_client_message_id(
            "  device-message-001  "
        )
        == "device-message-001"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_client_message_id_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_client_message_id(
            value
        )


def test_client_message_id_length_limit():
    allowed = (
        "x"
        * CHAT_CLIENT_MESSAGE_ID_MAX_LENGTH
    )

    assert (
        normalize_client_message_id(
            allowed
        )
        == allowed
    )

    with pytest.raises(
        ValueError
    ):
        normalize_client_message_id(
            allowed + "x"
        )


def test_client_message_id_rejects_control_characters():
    with pytest.raises(
        ValueError,
        match="control characters",
    ):
        normalize_client_message_id(
            "message-\x01-id"
        )


def test_message_body_normalizes_line_endings():
    body = normalize_message_body(
        "  Hello\r\nXuoroni\rChat  "
    )

    assert body == (
        "Hello\nXuoroni\nChat"
    )


def test_message_body_allows_tabs_and_newlines():
    body = normalize_message_body(
        "Hello\tXuoroni\nHow are you?"
    )

    assert body == (
        "Hello\tXuoroni\nHow are you?"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "\n\t\r\n",
        None,
        123,
    ],
)
def test_empty_or_non_text_message_body_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_message_body(
            value
        )


def test_message_body_length_limit():
    allowed = (
        "a"
        * CHAT_MESSAGE_MAX_LENGTH
    )

    assert (
        normalize_message_body(
            allowed
        )
        == allowed
    )

    with pytest.raises(
        ValueError
    ):
        normalize_message_body(
            allowed + "a"
        )


def test_message_body_rejects_invalid_control_character():
    with pytest.raises(
        ValueError,
        match="control characters",
    ):
        normalize_message_body(
            "Hello\x00Xuoroni"
        )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (
            1,
            1,
        ),
        (
            25,
            25,
        ),
        (
            "42",
            42,
        ),
        (
            " 7 ",
            7,
        ),
    ],
)
def test_message_sequence_normalization(
    value,
    expected,
):
    assert (
        normalize_message_sequence(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        True,
        False,
        "",
        "abc",
        None,
        1.5,
    ],
)
def test_invalid_message_sequence_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_message_sequence(
            value
        )


def test_chat_page_size_defaults():
    assert (
        normalize_chat_page_size()
        == CHAT_DEFAULT_PAGE_SIZE
    )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (
            1,
            1,
        ),
        (
            50,
            50,
        ),
        (
            "100",
            100,
        ),
    ],
)
def test_chat_page_size_normalization(
    value,
    expected,
):
    assert (
        normalize_chat_page_size(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        True,
        "101",
        101,
        "abc",
    ],
)
def test_invalid_chat_page_size_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_chat_page_size(
            value
        )


@pytest.mark.parametrize(
    (
        "value",
        "expected",
    ),
    [
        (
            None,
            None,
        ),
        (
            "",
            None,
        ),
        (
            9,
            9,
        ),
        (
            "14",
            14,
        ),
    ],
)
def test_before_sequence_normalization(
    value,
    expected,
):
    assert (
        normalize_before_sequence(
            value
        )
        == expected
    )


def test_conversation_document_has_expected_state():
    user_a = ObjectId()
    user_b = ObjectId()
    match_id = ObjectId()

    conversation = (
        build_conversation_document(
            match_id=match_id,
            first_user_id=user_a,
            second_user_id=user_b,
            now=FIXED_TIME,
        )
    )

    assert (
        conversation[
            "match_id"
        ]
        == match_id
    )

    assert set(
        conversation[
            "member_ids"
        ]
    ) == {
        user_a,
        user_b,
    }

    assert (
        conversation[
            "status"
        ]
        == "active"
    )

    assert (
        conversation[
            "last_sequence"
        ]
        == 0
    )

    assert (
        conversation[
            "last_message_id"
        ]
        is None
    )

    assert (
        conversation[
            "last_message_at"
        ]
        is None
    )

    assert (
        conversation[
            "last_message_preview"
        ]
        is None
    )

    assert (
        conversation[
            "created_at"
        ]
        == FIXED_TIME
    )

    assert (
        conversation[
            "updated_at"
        ]
        == FIXED_TIME
    )

    assert len(
        conversation[
            "member_states"
        ]
    ) == 2

    for state in conversation[
        "member_states"
    ]:
        assert (
            state[
                "unread_count"
            ]
            == 0
        )

        assert (
            state[
                "last_read_sequence"
            ]
            == 0
        )

        assert (
            state[
                "last_delivered_sequence"
            ]
            == 0
        )


def test_message_document_normalizes_input():
    conversation_id = ObjectId()
    match_id = ObjectId()
    sender_id = ObjectId()
    recipient_id = ObjectId()

    message = build_message_document(
        conversation_id=(
            str(conversation_id)
        ),
        match_id=str(
            match_id
        ),
        sender_id=str(
            sender_id
        ),
        recipient_id=str(
            recipient_id
        ),
        sequence="4",
        client_message_id=(
            " message-004 "
        ),
        body=(
            " Hello\r\nXuoroni "
        ),
        message_type=" TEXT ",
        now=FIXED_TIME,
    )

    assert (
        message[
            "conversation_id"
        ]
        == conversation_id
    )

    assert (
        message[
            "match_id"
        ]
        == match_id
    )

    assert (
        message[
            "sender_id"
        ]
        == sender_id
    )

    assert (
        message[
            "recipient_id"
        ]
        == recipient_id
    )

    assert (
        message[
            "sequence"
        ]
        == 4
    )

    assert (
        message[
            "client_message_id"
        ]
        == "message-004"
    )

    assert (
        message[
            "body"
        ]
        == "Hello\nXuoroni"
    )

    assert (
        message[
            "message_type"
        ]
        == "text"
    )

    assert (
        message[
            "moderation_status"
        ]
        == "not_reviewed"
    )

    assert (
        message[
            "delivered_at"
        ]
        is None
    )

    assert (
        message[
            "read_at"
        ]
        is None
    )

    assert (
        message[
            "sent_at"
        ]
        == FIXED_TIME
    )


def test_message_document_rejects_same_sender_and_recipient():
    user_id = ObjectId()

    with pytest.raises(
        ValueError,
        match="different users",
    ):
        build_message_document(
            conversation_id=ObjectId(),
            match_id=ObjectId(),
            sender_id=user_id,
            recipient_id=user_id,
            sequence=1,
            client_message_id="m-1",
            body="Hello",
        )


def test_delivery_status_progression():
    base = {
        "delivered_at": None,
        "read_at": None,
    }

    assert (
        get_message_delivery_status(
            base
        )
        == "sent"
    )

    delivered = {
        **base,
        "delivered_at": FIXED_TIME,
    }

    assert (
        get_message_delivery_status(
            delivered
        )
        == "delivered"
    )

    read = {
        **delivered,
        "read_at": FIXED_TIME,
    }

    assert (
        get_message_delivery_status(
            read
        )
        == "read"
    )


def test_read_status_has_priority_over_delivered():
    message = {
        "delivered_at": None,
        "read_at": FIXED_TIME,
    }

    assert (
        get_message_delivery_status(
            message
        )
        == "read"
    )


def test_serialize_conversation_for_member():
    user_a = ObjectId()
    user_b = ObjectId()
    match_id = ObjectId()
    conversation_id = ObjectId()
    message_id = ObjectId()

    conversation = (
        build_conversation_document(
            match_id=match_id,
            first_user_id=user_a,
            second_user_id=user_b,
            now=FIXED_TIME,
        )
    )

    conversation[
        "_id"
    ] = conversation_id

    conversation[
        "last_sequence"
    ] = 7

    conversation[
        "last_message_id"
    ] = message_id

    conversation[
        "last_message_at"
    ] = FIXED_TIME

    conversation[
        "last_message_preview"
    ] = "Hello"

    for state in conversation[
        "member_states"
    ]:
        if (
            state[
                "user_id"
            ]
            == user_a
        ):
            state[
                "unread_count"
            ] = 3

            state[
                "last_read_sequence"
            ] = 4

            state[
                "last_delivered_sequence"
            ] = 6

    payload = (
        serialize_conversation_for_user(
            conversation,
            user_a,
        )
    )

    assert (
        payload[
            "id"
        ]
        == str(
            conversation_id
        )
    )

    assert (
        payload[
            "match_id"
        ]
        == str(
            match_id
        )
    )

    assert (
        payload[
            "other_user_id"
        ]
        == str(
            user_b
        )
    )

    assert (
        payload[
            "unread_count"
        ]
        == 3
    )

    assert (
        payload[
            "last_read_sequence"
        ]
        == 4
    )

    assert (
        payload[
            "last_delivered_sequence"
        ]
        == 6
    )

    assert (
        payload[
            "last_sequence"
        ]
        == 7
    )

    assert (
        payload[
            "last_message_id"
        ]
        == str(
            message_id
        )
    )

    assert (
        payload[
            "last_message_at"
        ]
        == FIXED_TIME.isoformat()
    )


def test_serialize_conversation_uses_other_member_perspective():
    user_a = ObjectId()
    user_b = ObjectId()

    conversation = (
        build_conversation_document(
            match_id=ObjectId(),
            first_user_id=user_a,
            second_user_id=user_b,
            now=FIXED_TIME,
        )
    )

    conversation[
        "_id"
    ] = ObjectId()

    payload = (
        serialize_conversation_for_user(
            conversation,
            user_b,
        )
    )

    assert (
        payload[
            "other_user_id"
        ]
        == str(
            user_a
        )
    )


def test_serialize_conversation_rejects_outsider():
    user_a = ObjectId()
    user_b = ObjectId()

    conversation = (
        build_conversation_document(
            match_id=ObjectId(),
            first_user_id=user_a,
            second_user_id=user_b,
        )
    )

    conversation[
        "_id"
    ] = ObjectId()

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        serialize_conversation_for_user(
            conversation,
            ObjectId(),
        )


def test_missing_member_state_defaults_to_zero():
    user_a = ObjectId()
    user_b = ObjectId()

    conversation = (
        build_conversation_document(
            match_id=ObjectId(),
            first_user_id=user_a,
            second_user_id=user_b,
            now=FIXED_TIME,
        )
    )

    conversation[
        "_id"
    ] = ObjectId()

    conversation[
        "member_states"
    ] = []

    payload = (
        serialize_conversation_for_user(
            conversation,
            user_a,
        )
    )

    assert (
        payload[
            "unread_count"
        ]
        == 0
    )

    assert (
        payload[
            "last_read_sequence"
        ]
        == 0
    )

    assert (
        payload[
            "last_delivered_sequence"
        ]
        == 0
    )


def test_serialize_chat_message_is_json_safe():
    message = (
        build_message_document(
            conversation_id=ObjectId(),
            match_id=ObjectId(),
            sender_id=ObjectId(),
            recipient_id=ObjectId(),
            sequence=1,
            client_message_id="m-001",
            body="Hello",
            now=FIXED_TIME,
        )
    )

    message[
        "_id"
    ] = ObjectId()

    payload = serialize_chat_message(
        message
    )

    assert isinstance(
        payload[
            "id"
        ],
        str,
    )

    assert isinstance(
        payload[
            "conversation_id"
        ],
        str,
    )

    assert isinstance(
        payload[
            "match_id"
        ],
        str,
    )

    assert isinstance(
        payload[
            "sender_id"
        ],
        str,
    )

    assert isinstance(
        payload[
            "recipient_id"
        ],
        str,
    )

    assert (
        payload[
            "sent_at"
        ]
        == FIXED_TIME.isoformat()
    )

    assert (
        payload[
            "delivery_status"
        ]
        == "sent"
    )


def test_message_page_serializes_messages_and_cursor():
    message = (
        build_message_document(
            conversation_id=ObjectId(),
            match_id=ObjectId(),
            sender_id=ObjectId(),
            recipient_id=ObjectId(),
            sequence=9,
            client_message_id="m-009",
            body="Hello",
            now=FIXED_TIME,
        )
    )

    message[
        "_id"
    ] = ObjectId()

    page = build_message_page(
        messages=[
            message,
        ],
        has_more=True,
        next_before_sequence="9",
    )

    assert (
        len(
            page[
                "messages"
            ]
        )
        == 1
    )

    assert (
        page[
            "messages"
        ][
            0
        ][
            "sequence"
        ]
        == 9
    )

    assert (
        page[
            "has_more"
        ]
        is True
    )

    assert (
        page[
            "next_before_sequence"
        ]
        == 9
    )


@pytest.mark.parametrize(
    (
        "messages",
        "has_more",
        "next_before_sequence",
    ),
    [
        (
            {},
            False,
            None,
        ),
        (
            [],
            "false",
            None,
        ),
        (
            [],
            False,
            0,
        ),
    ],
)
def test_invalid_message_page_is_rejected(
    messages,
    has_more,
    next_before_sequence,
):
    with pytest.raises(
        ValueError
    ):
        build_message_page(
            messages=messages,
            has_more=has_more,
            next_before_sequence=(
                next_before_sequence
            ),
        )