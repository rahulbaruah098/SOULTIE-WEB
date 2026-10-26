"""Business-rule tests for Soultie chat service."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

import app.services.chat_service as chat_service
from app.services.chat_service import (
    ChatConflictError,
    ChatLifecycleError,
    ChatNotFoundError,
    ChatServiceError,
    ChatValidationError,
    close_chat_for_match,
    ensure_chat_for_match,
    get_my_conversation,
    list_my_conversations,
    list_my_messages,
    mark_chat_delivered,
    mark_chat_read,
    send_chat_message,
)


FIXED_TIME = datetime(
    2026,
    10,
    13,
    18,
    0,
    tzinfo=timezone.utc,
)


def _active_user(
    user_id,
    **overrides,
):
    user = {
        "_id": user_id,
        "account_status": "active",
    }

    user.update(
        overrides
    )

    return user


def _active_profile(
    user_id,
    **overrides,
):
    profile = {
        "_id": ObjectId(),
        "user_id": user_id,
        "onboarding_status": "completed",
        "profile_status": "active",
        "visibility": "visible",
    }

    profile.update(
        overrides
    )

    return profile


def _match(
    user_a,
    user_b,
    *,
    match_id=None,
    status="active",
):
    return {
        "_id": (
            match_id
            or ObjectId()
        ),
        "user_ids": [
            user_a,
            user_b,
        ],
        "status": status,
    }


def _conversation(
    user_a,
    user_b,
    *,
    match_id=None,
    conversation_id=None,
    status="active",
):
    match_object_id = (
        match_id
        or ObjectId()
    )

    return {
        "_id": (
            conversation_id
            or ObjectId()
        ),
        "match_id": match_object_id,
        "member_ids": [
            user_a,
            user_b,
        ],
        "status": status,
        "last_sequence": 0,
        "last_message_id": None,
        "last_message_at": None,
        "last_message_preview": None,
        "member_states": [
            {
                "user_id": user_a,
                "unread_count": 0,
                "last_read_sequence": 0,
                "last_delivered_sequence": 0,
            },
            {
                "user_id": user_b,
                "unread_count": 0,
                "last_read_sequence": 0,
                "last_delivered_sequence": 0,
            },
        ],
        "created_at": FIXED_TIME,
        "updated_at": FIXED_TIME,
    }


def _message(
    conversation,
    sender_id,
    recipient_id,
    *,
    sequence=1,
    client_message_id="client-001",
    body="Hello Xuoroni",
):
    return {
        "_id": ObjectId(),
        "conversation_id": (
            conversation[
                "_id"
            ]
        ),
        "match_id": (
            conversation[
                "match_id"
            ]
        ),
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "sequence": sequence,
        "client_message_id": (
            client_message_id
        ),
        "message_type": "text",
        "body": body,
        "sent_at": FIXED_TIME,
        "delivered_at": None,
        "read_at": None,
        "moderation_status": (
            "not_reviewed"
        ),
        "deleted_at": None,
    }


def _install_identity_state(
    monkeypatch,
    *,
    current_id,
    other_id,
    current_user="default",
    other_user="default",
    current_profile="default",
    other_profile="default",
    blocked=False,
):
    if current_user == "default":
        current_user = _active_user(
            current_id
        )

    if other_user == "default":
        other_user = _active_user(
            other_id
        )

    if current_profile == "default":
        current_profile = (
            _active_profile(
                current_id
            )
        )

    if other_profile == "default":
        other_profile = (
            _active_profile(
                other_id
            )
        )

    def fake_get_user(
        user_id,
    ):
        if user_id == current_id:
            return current_user

        if user_id == other_id:
            return other_user

        return None

    def fake_get_profile(
        user_id,
    ):
        if user_id == current_id:
            return current_profile

        if user_id == other_id:
            return other_profile

        return None

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        fake_get_user,
    )

    monkeypatch.setattr(
        chat_service,
        "get_profile_by_user_id",
        fake_get_profile,
    )

    monkeypatch.setattr(
        chat_service,
        "pair_is_blocked",
        lambda first, second: blocked,
    )


def test_chat_error_hierarchy():
    assert issubclass(
        ChatValidationError,
        ChatServiceError,
    )

    assert issubclass(
        ChatLifecycleError,
        ChatServiceError,
    )

    assert issubclass(
        ChatNotFoundError,
        ChatServiceError,
    )

    assert issubclass(
        ChatConflictError,
        ChatServiceError,
    )


def test_invalid_user_id_is_rejected():
    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        get_my_conversation(
            user_id="bad-user-id",
            conversation_id=ObjectId(),
        )

    assert (
        exc_info.value.code
        == "INVALID_ID"
    )

    assert (
        exc_info.value.status_code
        == 400
    )


def test_invalid_conversation_id_is_rejected():
    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        get_my_conversation(
            user_id=ObjectId(),
            conversation_id=(
                "bad-conversation"
            ),
        )

    assert (
        exc_info.value.code
        == "INVALID_ID"
    )


def test_missing_current_user_is_rejected(
    monkeypatch,
):
    current = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        lambda user_id: None,
    )

    with pytest.raises(
        ChatNotFoundError
    ) as exc_info:
        list_my_conversations(
            current
        )

    assert (
        exc_info.value.code
        == "CURRENT_USER_NOT_FOUND"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


def test_inactive_current_account_is_rejected(
    monkeypatch,
):
    current = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        lambda user_id: {
            "_id": current,
            "account_status": "paused",
        },
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        list_my_conversations(
            current
        )

    assert (
        exc_info.value.code
        == "CURRENT_ACCOUNT_INACTIVE"
    )

    assert (
        exc_info.value.status_code
        == 403
    )


def test_missing_current_profile_is_rejected(
    monkeypatch,
):
    current = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        lambda user_id: (
            _active_user(
                current
            )
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_profile_by_user_id",
        lambda user_id: None,
    )

    with pytest.raises(
        ChatNotFoundError
    ) as exc_info:
        list_my_conversations(
            current
        )

    assert (
        exc_info.value.code
        == "CURRENT_PROFILE_NOT_FOUND"
    )


def test_incomplete_onboarding_is_rejected(
    monkeypatch,
):
    current = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        lambda user_id: (
            _active_user(
                current
            )
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_profile_by_user_id",
        lambda user_id: (
            _active_profile(
                current,
                onboarding_status=(
                    "in_progress"
                ),
            )
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        list_my_conversations(
            current
        )

    assert (
        exc_info.value.code
        == "ONBOARDING_INCOMPLETE"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_inactive_current_profile_is_rejected(
    monkeypatch,
):
    current = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "get_user_by_id",
        lambda user_id: (
            _active_user(
                current
            )
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_profile_by_user_id",
        lambda user_id: (
            _active_profile(
                current,
                profile_status="paused",
            )
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        list_my_conversations(
            current
        )

    assert (
        exc_info.value.code
        == "CURRENT_PROFILE_INACTIVE"
    )


def test_ensure_chat_creates_for_active_match(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()
    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "create_conversation_if_absent",
        lambda **kwargs: {
            "conversation": conversation,
            "created": True,
        },
    )

    result = ensure_chat_for_match(
        user_id=current,
        match_id=match[
            "_id"
        ],
    )

    assert (
        result[
            "created"
        ]
        is True
    )

    assert (
        result[
            "conversation"
        ][
            "other_user_id"
        ]
        == str(
            other
        )
    )


def test_ensure_chat_is_idempotent(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "create_conversation_if_absent",
        lambda **kwargs: {
            "conversation": conversation,
            "created": False,
        },
    )

    result = ensure_chat_for_match(
        user_id=current,
        match_id=match[
            "_id"
        ],
    )

    assert (
        result[
            "created"
        ]
        is False
    )


def test_ensure_chat_requires_existing_match(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: None,
    )

    with pytest.raises(
        ChatNotFoundError
    ) as exc_info:
        ensure_chat_for_match(
            user_id=current,
            match_id=ObjectId(),
        )

    assert (
        exc_info.value.code
        == "MATCH_NOT_FOUND"
    )


def test_ensure_chat_requires_active_match(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
        status="unmatched",
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        ensure_chat_for_match(
            user_id=current,
            match_id=match[
                "_id"
            ],
        )

    assert (
        exc_info.value.code
        == "MATCH_INACTIVE"
    )


def test_blocked_pair_cannot_create_chat(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
        blocked=True,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        ensure_chat_for_match(
            user_id=current,
            match_id=match[
                "_id"
            ],
        )

    assert (
        exc_info.value.code
        == "CHAT_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    "other_user,other_profile",
    [
        (
            None,
            "default",
        ),
        (
            {
                "_id": ObjectId(),
                "account_status": "paused",
            },
            "default",
        ),
        (
            "default",
            None,
        ),
        (
            "default",
            {
                "_id": ObjectId(),
                "onboarding_status": (
                    "completed"
                ),
                "profile_status": "paused",
            },
        ),
    ],
)
def test_unavailable_target_cannot_create_chat(
    monkeypatch,
    other_user,
    other_profile,
):
    current = ObjectId()
    other = ObjectId()

    if isinstance(
        other_user,
        dict,
    ):
        other_user = {
            **other_user,
            "_id": other,
        }

    if isinstance(
        other_profile,
        dict,
    ):
        other_profile = {
            **other_profile,
            "user_id": other,
        }

    match = _match(
        current,
        other,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
        other_user=other_user,
        other_profile=other_profile,
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        ensure_chat_for_match(
            user_id=current,
            match_id=match[
                "_id"
            ],
        )

    assert (
        exc_info.value.code
        == "CHAT_UNAVAILABLE"
    )


def test_list_my_conversations_serializes_results(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    conversation = _conversation(
        current,
        other,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    captured = {}

    def fake_list(
        user_id,
        *,
        status,
        limit,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "status"
        ] = status

        captured[
            "limit"
        ] = limit

        return [
            conversation,
        ]

    monkeypatch.setattr(
        chat_service,
        "list_conversations_for_user",
        fake_list,
    )

    result = list_my_conversations(
        current,
        status=" ACTIVE ",
        limit="20",
    )

    assert (
        captured[
            "status"
        ]
        == "active"
    )

    assert (
        captured[
            "limit"
        ]
        == 20
    )

    assert (
        result[
            "returned"
        ]
        == 1
    )

    assert (
        result[
            "conversations"
        ][
            0
        ][
            "other_user_id"
        ]
        == str(
            other
        )
    )


@pytest.mark.parametrize(
    (
        "status",
        "limit",
        "expected_code",
    ),
    [
        (
            "paused",
            20,
            "INVALID_CONVERSATION_STATUS",
        ),
        (
            "active",
            0,
            "INVALID_LIMIT",
        ),
        (
            "active",
            101,
            "INVALID_LIMIT",
        ),
        (
            "active",
            True,
            "INVALID_LIMIT",
        ),
    ],
)
def test_conversation_list_validation(
    monkeypatch,
    status,
    limit,
    expected_code,
):
    current = ObjectId()
    other = ObjectId()

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        list_my_conversations(
            current,
            status=status,
            limit=limit,
        )

    assert (
        exc_info.value.code
        == expected_code
    )


def test_get_my_conversation_requires_membership(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()
    conversation_id = ObjectId()

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: None,
    )

    with pytest.raises(
        ChatNotFoundError
    ) as exc_info:
        get_my_conversation(
            user_id=current,
            conversation_id=(
                conversation_id
            ),
        )

    assert (
        exc_info.value.code
        == "CONVERSATION_NOT_FOUND"
    )


def test_get_my_conversation_success(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    conversation = _conversation(
        current,
        other,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    result = get_my_conversation(
        user_id=current,
        conversation_id=(
            conversation[
                "_id"
            ]
        ),
    )

    assert (
        result[
            "other_user_id"
        ]
        == str(
            other
        )
    )


def test_list_messages_returns_serialized_page(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    conversation = _conversation(
        current,
        other,
    )

    message = _message(
        conversation,
        other,
        current,
        sequence=4,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    captured = {}

    def fake_list_messages(
        conversation_id,
        user_id,
        *,
        before_sequence,
        limit,
    ):
        captured[
            "before_sequence"
        ] = before_sequence

        captured[
            "limit"
        ] = limit

        return {
            "conversation": conversation,
            "messages": [
                message,
            ],
            "has_more": True,
            "next_before_sequence": 4,
        }

    monkeypatch.setattr(
        chat_service,
        "list_messages_for_conversation",
        fake_list_messages,
    )

    result = list_my_messages(
        user_id=current,
        conversation_id=(
            conversation[
                "_id"
            ]
        ),
        before_sequence="10",
        limit="25",
    )

    assert (
        captured[
            "before_sequence"
        ]
        == 10
    )

    assert (
        captured[
            "limit"
        ]
        == 25
    )

    assert (
        result[
            "messages"
        ][
            0
        ][
            "sequence"
        ]
        == 4
    )

    assert (
        result[
            "has_more"
        ]
        is True
    )


@pytest.mark.parametrize(
    (
        "before_sequence",
        "limit",
        "expected_code",
    ),
    [
        (
            0,
            20,
            "INVALID_MESSAGE_CURSOR",
        ),
        (
            True,
            20,
            "INVALID_MESSAGE_CURSOR",
        ),
        (
            "abc",
            20,
            "INVALID_MESSAGE_CURSOR",
        ),
        (
            None,
            0,
            "INVALID_LIMIT",
        ),
    ],
)
def test_message_list_validation(
    monkeypatch,
    before_sequence,
    limit,
    expected_code,
):
    current = ObjectId()
    other = ObjectId()

    conversation = _conversation(
        current,
        other,
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        list_my_messages(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            before_sequence=(
                before_sequence
            ),
            limit=limit,
        )

    assert (
        exc_info.value.code
        == expected_code
    )


def test_send_message_success(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    message = _message(
        conversation,
        current,
        other,
        body="Hello Xuoroni",
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    captured = {}

    def fake_create_message(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return {
            "message": message,
            "created": True,
        }

    monkeypatch.setattr(
        chat_service,
        "create_message_idempotent",
        fake_create_message,
    )

    result = send_chat_message(
        user_id=current,
        conversation_id=(
            conversation[
                "_id"
            ]
        ),
        client_message_id=(
            "  client-001  "
        ),
        body="  Hello Xuoroni  ",
        message_type=" TEXT ",
    )

    assert (
        captured[
            "client_message_id"
        ]
        == "client-001"
    )

    assert (
        captured[
            "body"
        ]
        == "Hello Xuoroni"
    )

    assert (
        captured[
            "message_type"
        ]
        == "text"
    )

    assert (
        captured[
            "recipient_id"
        ]
        == other
    )

    assert (
        result[
            "created"
        ]
        is True
    )


@pytest.mark.parametrize(
    (
        "client_message_id",
        "body",
        "message_type",
    ),
    [
        (
            "",
            "Hello",
            "text",
        ),
        (
            "client-1",
            "   ",
            "text",
        ),
        (
            "client-1",
            "Hello",
            "video",
        ),
    ],
)
def test_send_message_validation(
    monkeypatch,
    client_message_id,
    body,
    message_type,
):
    current = ObjectId()
    other = ObjectId()

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=ObjectId(),
            client_message_id=(
                client_message_id
            ),
            body=body,
            message_type=(
                message_type
            ),
        )

    assert (
        exc_info.value.code
        == "INVALID_MESSAGE"
    )


def test_send_message_maps_idempotency_conflict(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    def conflict(
        **kwargs,
    ):
        raise ValueError(
            (
                "client_message_id was "
                "already used with "
                "different message content."
            )
        )

    monkeypatch.setattr(
        chat_service,
        "create_message_idempotent",
        conflict,
    )

    with pytest.raises(
        ChatConflictError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            client_message_id="dup-1",
            body="Hello",
        )

    assert (
        exc_info.value.code
        == "MESSAGE_IDEMPOTENCY_CONFLICT"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_blocked_pair_cannot_send_message(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
        blocked=True,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            client_message_id="m-1",
            body="Hello",
        )

    assert (
        exc_info.value.code
        == "CHAT_UNAVAILABLE"
    )


def test_closed_conversation_cannot_send(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    conversation = _conversation(
        current,
        other,
        status="closed",
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            client_message_id="m-1",
            body="Hello",
        )

    assert (
        exc_info.value.code
        == "CONVERSATION_CLOSED"
    )


def test_inactive_match_closes_conversation_on_write(
    monkeypatch,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
        status="unmatched",
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    closed = {}

    def fake_close(
        match_id,
        *,
        reason,
    ):
        closed[
            "match_id"
        ] = match_id

        closed[
            "reason"
        ] = reason

        return conversation

    monkeypatch.setattr(
        chat_service,
        "close_conversation_by_match_id",
        fake_close,
    )

    with pytest.raises(
        ChatLifecycleError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            client_message_id="m-1",
            body="Hello",
        )

    assert (
        exc_info.value.code
        == "MATCH_INACTIVE"
    )

    assert (
        closed[
            "match_id"
        ]
        == match[
            "_id"
        ]
    )

    assert (
        closed[
            "reason"
        ]
        == "match_inactive"
    )


def test_conversation_match_membership_mismatch_fails_closed(
    monkeypatch,
):
    current = ObjectId()
    conversation_other = ObjectId()
    match_other = ObjectId()

    match = _match(
        current,
        match_other,
    )

    conversation = _conversation(
        current,
        conversation_other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=(
            conversation_other
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    with pytest.raises(
        ChatConflictError
    ) as exc_info:
        send_chat_message(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            client_message_id="m-1",
            body="Hello",
        )

    assert (
        exc_info.value.code
        == "CHAT_STATE_INVALID"
    )


@pytest.mark.parametrize(
    (
        "function_name",
        "repository_name",
    ),
    [
        (
            "delivered",
            "mark_messages_delivered",
        ),
        (
            "read",
            "mark_messages_read",
        ),
    ],
)
def test_receipts_succeed(
    monkeypatch,
    function_name,
    repository_name,
):
    current = ObjectId()
    other = ObjectId()

    match = _match(
        current,
        other,
    )

    conversation = _conversation(
        current,
        other,
        match_id=match[
            "_id"
        ],
    )

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    monkeypatch.setattr(
        chat_service,
        "get_conversation_by_id_for_user",
        lambda conversation_id, user_id: (
            conversation
        ),
    )

    monkeypatch.setattr(
        chat_service,
        "get_match_by_id_for_user",
        lambda match_id, user_id: (
            match
        ),
    )

    captured = {}

    def fake_receipt(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return {
            "conversation": conversation,
            "through_sequence": 3,
            "modified_count": 2,
        }

    monkeypatch.setattr(
        chat_service,
        repository_name,
        fake_receipt,
    )

    if function_name == "delivered":
        result = mark_chat_delivered(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            through_sequence="3",
        )

    else:
        result = mark_chat_read(
            user_id=current,
            conversation_id=(
                conversation[
                    "_id"
                ]
            ),
            through_sequence="3",
        )

    assert (
        captured[
            "through_sequence"
        ]
        == 3
    )

    assert (
        result[
            "through_sequence"
        ]
        == 3
    )

    assert (
        result[
            "modified_count"
        ]
        == 2
    )


@pytest.mark.parametrize(
    "receipt_function",
    [
        mark_chat_delivered,
        mark_chat_read,
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [
        0,
        -1,
        True,
        "abc",
        "1.5",
    ],
)
def test_receipt_sequence_validation(
    monkeypatch,
    receipt_function,
    invalid,
):
    current = ObjectId()
    other = ObjectId()

    _install_identity_state(
        monkeypatch,
        current_id=current,
        other_id=other,
    )

    with pytest.raises(
        ChatValidationError
    ) as exc_info:
        receipt_function(
            user_id=current,
            conversation_id=ObjectId(),
            through_sequence=invalid,
        )

    assert (
        exc_info.value.code
        == "INVALID_RECEIPT_SEQUENCE"
    )


def test_close_chat_for_match_returns_internal_summary(
    monkeypatch,
):
    match_id = ObjectId()
    conversation_id = ObjectId()

    monkeypatch.setattr(
        chat_service,
        "close_conversation_by_match_id",
        lambda match_id, reason: {
            "_id": conversation_id,
            "match_id": match_id,
            "status": "closed",
            "closed_reason": reason,
        },
    )

    result = close_chat_for_match(
        match_id=match_id,
        reason="unmatched",
    )

    assert result == {
        "conversation_id": str(
            conversation_id
        ),
        "match_id": str(
            match_id
        ),
        "status": "closed",
        "closed_reason": "unmatched",
    }


def test_close_chat_for_missing_conversation_returns_none(
    monkeypatch,
):
    monkeypatch.setattr(
        chat_service,
        "close_conversation_by_match_id",
        lambda match_id, reason: None,
    )

    result = close_chat_for_match(
        match_id=ObjectId(),
    )

    assert result is None