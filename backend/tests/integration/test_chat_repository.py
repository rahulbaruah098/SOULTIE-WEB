"""MongoDB integration tests for Soultie chat persistence."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.chat_repository import (
    close_conversation_by_match_id,
    create_conversation_if_absent,
    create_message_idempotent,
    get_conversation_by_id,
    get_conversation_by_id_for_user,
    get_conversation_by_match_id,
    get_message_by_id,
    get_message_by_sender_client_id,
    list_conversations_for_user,
    list_messages_for_conversation,
    mark_messages_delivered,
    mark_messages_read,
)


TEST_DATABASE = (
    "xuoroni_test_chat_repository"
)

BASE_TIME = datetime(
    2026,
    10,
    13,
    18,
    0,
    tzinfo=timezone.utc,
)


def _as_utc(
    value,
):
    """Normalize MongoDB datetime values for UTC comparison."""

    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


@pytest.fixture(scope="module")
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

    yield application

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture(autouse=True)
def clean_chat_state(
    app,
):
    with app.app_context():
        mongo.db.messages.delete_many(
            {}
        )

        mongo.db.conversations.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.messages.delete_many(
            {}
        )

        mongo.db.conversations.delete_many(
            {}
        )


def _create_conversation(
    *,
    user_a=None,
    user_b=None,
    match_id=None,
    now=BASE_TIME,
):
    first = (
        user_a
        or ObjectId()
    )

    second = (
        user_b
        or ObjectId()
    )

    match = (
        match_id
        or ObjectId()
    )

    result = (
        create_conversation_if_absent(
            match_id=match,
            first_user_id=first,
            second_user_id=second,
            now=now,
        )
    )

    assert (
        result[
            "created"
        ]
        is True
    )

    conversation = (
        result[
            "conversation"
        ]
    )

    assert conversation is not None

    return {
        "conversation": conversation,
        "user_a": first,
        "user_b": second,
        "match_id": match,
    }


def _member_state(
    conversation,
    user_id,
):
    for state in conversation[
        "member_states"
    ]:
        if (
            state[
                "user_id"
            ]
            == user_id
        ):
            return state

    raise AssertionError(
        "Conversation member state was not found."
    )


def _send(
    *,
    conversation,
    sender,
    recipient,
    client_message_id,
    body="Hello from Xuoroni",
    now=BASE_TIME,
):
    return create_message_idempotent(
        conversation_id=(
            conversation[
                "_id"
            ]
        ),
        sender_id=sender,
        recipient_id=recipient,
        client_message_id=(
            client_message_id
        ),
        body=body,
        message_type="text",
        now=now,
    )


def test_create_conversation_is_idempotent(
    app,
):
    user_a = ObjectId()
    user_b = ObjectId()
    match_id = ObjectId()

    with app.app_context():
        first = (
            create_conversation_if_absent(
                match_id=match_id,
                first_user_id=user_a,
                second_user_id=user_b,
                now=BASE_TIME,
            )
        )

        second = (
            create_conversation_if_absent(
                match_id=match_id,
                first_user_id=user_b,
                second_user_id=user_a,
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=10
                    )
                ),
            )
        )

        assert (
            first[
                "created"
            ]
            is True
        )

        assert (
            second[
                "created"
            ]
            is False
        )

        assert (
            first[
                "conversation"
            ][
                "_id"
            ]
            == second[
                "conversation"
            ][
                "_id"
            ]
        )

        assert (
            mongo.db.conversations.count_documents(
                {}
            )
            == 1
        )

        assert set(
            first[
                "conversation"
            ][
                "member_ids"
            ]
        ) == {
            user_a,
            user_b,
        }


def test_conversation_lookup_enforces_membership(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        conversation = (
            context[
                "conversation"
            ]
        )

        assert (
            get_conversation_by_id(
                conversation[
                    "_id"
                ]
            )[
                "_id"
            ]
            == conversation[
                "_id"
            ]
        )

        assert (
            get_conversation_by_match_id(
                context[
                    "match_id"
                ]
            )[
                "_id"
            ]
            == conversation[
                "_id"
            ]
        )

        assert (
            get_conversation_by_id_for_user(
                conversation[
                    "_id"
                ],
                context[
                    "user_a"
                ],
            )[
                "_id"
            ]
            == conversation[
                "_id"
            ]
        )

        assert (
            get_conversation_by_id_for_user(
                conversation[
                    "_id"
                ],
                ObjectId(),
            )
            is None
        )

        assert (
            get_conversation_by_id(
                "not-an-object-id"
            )
            is None
        )

        assert (
            get_conversation_by_match_id(
                "not-an-object-id"
            )
            is None
        )


def test_create_message_allocates_sequence_and_unread_state(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        result = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="message-001",
            body="Hello there",
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        message = (
            result[
                "message"
            ]
        )

        assert (
            message[
                "sequence"
            ]
            == 1
        )

        assert (
            message[
                "body"
            ]
            == "Hello there"
        )

        conversation = (
            get_conversation_by_id(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            )
        )

        assert (
            conversation[
                "last_sequence"
            ]
            == 1
        )

        assert (
            conversation[
                "last_message_sequence"
            ]
            == 1
        )

        assert (
            conversation[
                "last_message_id"
            ]
            == message[
                "_id"
            ]
        )

        assert (
            conversation[
                "last_message_preview"
            ]
            == "Hello there"
        )

        sender_state = (
            _member_state(
                conversation,
                context[
                    "user_a"
                ],
            )
        )

        recipient_state = (
            _member_state(
                conversation,
                context[
                    "user_b"
                ],
            )
        )

        assert (
            sender_state[
                "unread_count"
            ]
            == 0
        )

        assert (
            recipient_state[
                "unread_count"
            ]
            == 1
        )


def test_message_sequences_are_monotonic(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        first = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="seq-001",
            now=BASE_TIME,
        )

        second = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_b"
                ]
            ),
            recipient=(
                context[
                    "user_a"
                ]
            ),
            client_message_id="seq-002",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        third = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="seq-003",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=2
                )
            ),
        )

        assert [
            first[
                "message"
            ][
                "sequence"
            ],
            second[
                "message"
            ][
                "sequence"
            ],
            third[
                "message"
            ][
                "sequence"
            ],
        ] == [
            1,
            2,
            3,
        ]


def test_same_message_retry_is_idempotent(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        first = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "retry-message-001"
            ),
            body="Same message",
        )

        second = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "retry-message-001"
            ),
            body="Same message",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=20
                )
            ),
        )

        assert (
            first[
                "created"
            ]
            is True
        )

        assert (
            second[
                "created"
            ]
            is False
        )

        assert (
            second[
                "message"
            ][
                "_id"
            ]
            == first[
                "message"
            ][
                "_id"
            ]
        )

        assert (
            mongo.db.messages.count_documents(
                {}
            )
            == 1
        )

        conversation = (
            get_conversation_by_id(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            )
        )

        assert (
            conversation[
                "last_sequence"
            ]
            == 1
        )

        recipient_state = (
            _member_state(
                conversation,
                context[
                    "user_b"
                ],
            )
        )

        assert (
            recipient_state[
                "unread_count"
            ]
            == 1
        )


def test_idempotency_key_rejects_different_content(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "same-key"
            ),
            body="Original message",
        )

        with pytest.raises(
            ValueError,
            match=(
                "different message content"
            ),
        ):
            _send(
                conversation=(
                    context[
                        "conversation"
                    ]
                ),
                sender=(
                    context[
                        "user_a"
                    ]
                ),
                recipient=(
                    context[
                        "user_b"
                    ]
                ),
                client_message_id=(
                    "same-key"
                ),
                body="Changed message",
            )

        conversation = (
            get_conversation_by_id(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            )
        )

        assert (
            conversation[
                "last_sequence"
            ]
            == 1
        )

        assert (
            mongo.db.messages.count_documents(
                {}
            )
            == 1
        )


def test_idempotency_key_is_not_reusable_across_conversations(
    app,
):
    sender = ObjectId()

    with app.app_context():
        first_context = (
            _create_conversation(
                user_a=sender,
                user_b=ObjectId(),
            )
        )

        second_context = (
            _create_conversation(
                user_a=sender,
                user_b=ObjectId(),
            )
        )

        _send(
            conversation=(
                first_context[
                    "conversation"
                ]
            ),
            sender=sender,
            recipient=(
                first_context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "global-device-key-001"
            ),
        )

        with pytest.raises(
            ValueError,
            match=(
                "another conversation"
            ),
        ):
            _send(
                conversation=(
                    second_context[
                        "conversation"
                    ]
                ),
                sender=sender,
                recipient=(
                    second_context[
                        "user_b"
                    ]
                ),
                client_message_id=(
                    "global-device-key-001"
                ),
            )

        assert (
            second_context[
                "conversation"
            ][
                "last_sequence"
            ]
            == 0
        )

        reloaded = (
            get_conversation_by_id(
                second_context[
                    "conversation"
                ][
                    "_id"
                ]
            )
        )

        assert (
            reloaded[
                "last_sequence"
            ]
            == 0
        )


def test_invalid_message_does_not_consume_sequence(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        with pytest.raises(
            ValueError
        ):
            _send(
                conversation=(
                    context[
                        "conversation"
                    ]
                ),
                sender=(
                    context[
                        "user_a"
                    ]
                ),
                recipient=(
                    context[
                        "user_b"
                    ]
                ),
                client_message_id=(
                    "invalid-body-001"
                ),
                body="   ",
            )

        conversation = (
            get_conversation_by_id(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            )
        )

        assert (
            conversation[
                "last_sequence"
            ]
            == 0
        )

        assert (
            mongo.db.messages.count_documents(
                {}
            )
            == 0
        )


def test_message_lookup_by_sender_client_id(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        result = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "  lookup-key-001  "
            ),
        )

        found = (
            get_message_by_sender_client_id(
                context[
                    "user_a"
                ],
                "lookup-key-001",
            )
        )

        assert (
            found[
                "_id"
            ]
            == result[
                "message"
            ][
                "_id"
            ]
        )

        assert (
            get_message_by_id(
                result[
                    "message"
                ][
                    "_id"
                ]
            )[
                "_id"
            ]
            == found[
                "_id"
            ]
        )

        assert (
            get_message_by_id(
                "not-an-object-id"
            )
            is None
        )


def test_message_cursor_pagination_is_stable(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        for number in range(
            1,
            6,
        ):
            _send(
                conversation=(
                    context[
                        "conversation"
                    ]
                ),
                sender=(
                    context[
                        "user_a"
                    ]
                ),
                recipient=(
                    context[
                        "user_b"
                    ]
                ),
                client_message_id=(
                    f"page-{number}"
                ),
                body=(
                    f"Message {number}"
                ),
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=number
                    )
                ),
            )

        first_page = (
            list_messages_for_conversation(
                context[
                    "conversation"
                ][
                    "_id"
                ],
                context[
                    "user_a"
                ],
                limit=2,
            )
        )

        assert [
            message[
                "sequence"
            ]
            for message
            in first_page[
                "messages"
            ]
        ] == [
            4,
            5,
        ]

        assert (
            first_page[
                "has_more"
            ]
            is True
        )

        assert (
            first_page[
                "next_before_sequence"
            ]
            == 4
        )

        second_page = (
            list_messages_for_conversation(
                context[
                    "conversation"
                ][
                    "_id"
                ],
                context[
                    "user_a"
                ],
                before_sequence=(
                    first_page[
                        "next_before_sequence"
                    ]
                ),
                limit=2,
            )
        )

        assert [
            message[
                "sequence"
            ]
            for message
            in second_page[
                "messages"
            ]
        ] == [
            2,
            3,
        ]

        assert (
            second_page[
                "has_more"
            ]
            is True
        )

        assert (
            second_page[
                "next_before_sequence"
            ]
            == 2
        )

        third_page = (
            list_messages_for_conversation(
                context[
                    "conversation"
                ][
                    "_id"
                ],
                context[
                    "user_a"
                ],
                before_sequence=(
                    second_page[
                        "next_before_sequence"
                    ]
                ),
                limit=2,
            )
        )

        assert [
            message[
                "sequence"
            ]
            for message
            in third_page[
                "messages"
            ]
        ] == [
            1,
        ]

        assert (
            third_page[
                "has_more"
            ]
            is False
        )

        assert (
            third_page[
                "next_before_sequence"
            ]
            is None
        )


def test_outsider_cannot_list_conversation_messages(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "private-message"
            ),
        )

        result = (
            list_messages_for_conversation(
                context[
                    "conversation"
                ][
                    "_id"
                ],
                ObjectId(),
            )
        )

        assert result is None


def test_mark_messages_delivered_is_directional(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        first = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="delivery-1",
            now=BASE_TIME,
        )

        second = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="delivery-2",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        reverse = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_b"
                ]
            ),
            recipient=(
                context[
                    "user_a"
                ]
            ),
            client_message_id="delivery-reverse",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=2
                )
            ),
        )

        receipt_time = (
            BASE_TIME
            + timedelta(
                minutes=1
            )
        )

        result = (
            mark_messages_delivered(
                conversation_id=(
                    context[
                        "conversation"
                    ][
                        "_id"
                    ]
                ),
                user_id=(
                    context[
                        "user_b"
                    ]
                ),
                through_sequence=1,
                now=receipt_time,
            )
        )

        assert (
            result[
                "through_sequence"
            ]
            == 1
        )

        assert (
            result[
                "modified_count"
            ]
            == 1
        )

        first_reloaded = (
            get_message_by_id(
                first[
                    "message"
                ][
                    "_id"
                ]
            )
        )

        second_reloaded = (
            get_message_by_id(
                second[
                    "message"
                ][
                    "_id"
                ]
            )
        )

        reverse_reloaded = (
            get_message_by_id(
                reverse[
                    "message"
                ][
                    "_id"
                ]
            )
        )

        assert (
            _as_utc(
                first_reloaded[
                    "delivered_at"
                ]
            )
            == receipt_time
        )

        assert (
            second_reloaded[
                "delivered_at"
            ]
            is None
        )

        assert (
            reverse_reloaded[
                "delivered_at"
            ]
            is None
        )

        recipient_state = (
            _member_state(
                result[
                    "conversation"
                ],
                context[
                    "user_b"
                ],
            )
        )

        assert (
            recipient_state[
                "last_delivered_sequence"
            ]
            == 1
        )


def test_mark_read_preserves_original_delivery_timestamp(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        message_result = _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="read-1",
        )

        delivered_at = (
            BASE_TIME
            + timedelta(
                minutes=1
            )
        )

        read_at = (
            BASE_TIME
            + timedelta(
                minutes=2
            )
        )

        mark_messages_delivered(
            conversation_id=(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            ),
            user_id=(
                context[
                    "user_b"
                ]
            ),
            through_sequence=1,
            now=delivered_at,
        )

        result = mark_messages_read(
            conversation_id=(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            ),
            user_id=(
                context[
                    "user_b"
                ]
            ),
            through_sequence=1,
            now=read_at,
        )

        message = get_message_by_id(
            message_result[
                "message"
            ][
                "_id"
            ]
        )

        assert (
            _as_utc(
                message[
                    "delivered_at"
                ]
            )
            == delivered_at
        )

        assert (
            _as_utc(
                message[
                    "read_at"
                ]
            )
            == read_at
        )

        state = _member_state(
            result[
                "conversation"
            ],
            context[
                "user_b"
            ],
        )

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
            == 1
        )

        assert (
            state[
                "last_delivered_sequence"
            ]
            == 1
        )


def test_partial_read_keeps_remaining_unread_count(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="partial-1",
            now=BASE_TIME,
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="partial-2",
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        result = mark_messages_read(
            conversation_id=(
                context[
                    "conversation"
                ][
                    "_id"
                ]
            ),
            user_id=(
                context[
                    "user_b"
                ]
            ),
            through_sequence=1,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        state = _member_state(
            result[
                "conversation"
            ],
            context[
                "user_b"
            ],
        )

        assert (
            state[
                "unread_count"
            ]
            == 1
        )

        assert (
            state[
                "last_read_sequence"
            ]
            == 1
        )

        messages = list(
            mongo.db.messages.find(
                {}
            ).sort(
                "sequence",
                1,
            )
        )

        assert (
            messages[
                0
            ][
                "read_at"
            ]
            is not None
        )

        assert (
            messages[
                1
            ][
                "read_at"
            ]
            is None
        )


def test_receipt_sequence_caps_at_conversation_maximum(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="cap-1",
        )

        result = (
            mark_messages_delivered(
                conversation_id=(
                    context[
                        "conversation"
                    ][
                        "_id"
                    ]
                ),
                user_id=(
                    context[
                        "user_b"
                    ]
                ),
                through_sequence=999,
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        assert (
            result[
                "through_sequence"
            ]
            == 1
        )


@pytest.mark.parametrize(
    "invalid",
    [
        0,
        -1,
        True,
        False,
        1.5,
        "1.5",
        "abc",
    ],
)
def test_invalid_receipt_sequence_is_rejected(
    app,
    invalid,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "receipt-validation"
            ),
        )

        with pytest.raises(
            ValueError,
            match="positive integer",
        ):
            mark_messages_delivered(
                conversation_id=(
                    context[
                        "conversation"
                    ][
                        "_id"
                    ]
                ),
                user_id=(
                    context[
                        "user_b"
                    ]
                ),
                through_sequence=invalid,
            )


def test_conversation_list_orders_by_latest_activity(
    app,
):
    user = ObjectId()

    with app.app_context():
        older = _create_conversation(
            user_a=user,
            user_b=ObjectId(),
            now=BASE_TIME,
        )

        newer = _create_conversation(
            user_a=user,
            user_b=ObjectId(),
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        _send(
            conversation=(
                older[
                    "conversation"
                ]
            ),
            sender=user,
            recipient=(
                older[
                    "user_b"
                ]
            ),
            client_message_id="older-message",
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        _send(
            conversation=(
                newer[
                    "conversation"
                ]
            ),
            sender=user,
            recipient=(
                newer[
                    "user_b"
                ]
            ),
            client_message_id="newer-message",
            now=(
                BASE_TIME
                + timedelta(
                    minutes=2
                )
            ),
        )

        conversations = (
            list_conversations_for_user(
                user,
                limit=10,
            )
        )

        assert [
            conversation[
                "_id"
            ]
            for conversation
            in conversations
        ] == [
            newer[
                "conversation"
            ][
                "_id"
            ],
            older[
                "conversation"
            ][
                "_id"
            ],
        ]


def test_close_conversation_is_idempotent_and_stops_writes(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        conversation_id = (
            context[
                "conversation"
            ][
                "_id"
            ]
        )

        closed_at = (
            BASE_TIME
            + timedelta(
                minutes=10
            )
        )

        first = (
            close_conversation_by_match_id(
                context[
                    "match_id"
                ],
                reason="unmatched",
                now=closed_at,
            )
        )

        second = (
            close_conversation_by_match_id(
                context[
                    "match_id"
                ],
                reason="different-retry-reason",
                now=(
                    closed_at
                    + timedelta(
                        seconds=10
                    )
                ),
            )
        )

        assert (
            first[
                "status"
            ]
            == "closed"
        )

        assert (
            first[
                "closed_reason"
            ]
            == "unmatched"
        )

        assert (
            _as_utc(
                first[
                    "closed_at"
                ]
            )
            == closed_at
        )

        assert (
            second[
                "_id"
            ]
            == first[
                "_id"
            ]
        )

        assert (
            second[
                "closed_reason"
            ]
            == "unmatched"
        )

        result = _send(
            conversation=first,
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id=(
                "after-close-message"
            ),
        )

        assert (
            result[
                "created"
            ]
            is False
        )

        assert (
            result[
                "message"
            ]
            is None
        )

        reloaded = (
            get_conversation_by_id(
                conversation_id
            )
        )

        assert (
            reloaded[
                "last_sequence"
            ]
            == 0
        )

        assert (
            mongo.db.messages.count_documents(
                {}
            )
            == 0
        )


def test_receipts_require_conversation_membership(
    app,
):
    with app.app_context():
        context = (
            _create_conversation()
        )

        _send(
            conversation=(
                context[
                    "conversation"
                ]
            ),
            sender=(
                context[
                    "user_a"
                ]
            ),
            recipient=(
                context[
                    "user_b"
                ]
            ),
            client_message_id="membership-1",
        )

        outsider = ObjectId()

        assert (
            mark_messages_delivered(
                conversation_id=(
                    context[
                        "conversation"
                    ][
                        "_id"
                    ]
                ),
                user_id=outsider,
            )
            is None
        )

        assert (
            mark_messages_read(
                conversation_id=(
                    context[
                        "conversation"
                    ][
                        "_id"
                    ]
                ),
                user_id=outsider,
            )
            is None
        )