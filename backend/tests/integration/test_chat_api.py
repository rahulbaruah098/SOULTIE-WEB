"""Authenticated HTTP tests for Soultie chat."""

from uuid import uuid4

import pytest
from bson import ObjectId

from app import create_app
from app.api.v1.chats import (
    routes as chat_routes,
)
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.services.chat_service import (
    ChatConflictError,
    ChatNotFoundError,
)


TEST_DATABASE = (
    "xuoroni_test_chat_api"
)


@pytest.fixture()
def app():
    otp_prefix = (
        "xuoroni:test:chat-api:"
        + uuid4().hex
        + ":"
    )

    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-chat-api-test-"
                "secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "EXPOSE_DEV_OTP": True,
            "DEV_OTP_CODE": "654321",
            "OTP_TTL_SECONDS": 60,
            "OTP_RESEND_COOLDOWN_SECONDS": 1,
            "OTP_MAX_ATTEMPTS": 3,
            "OTP_REQUEST_WINDOW_SECONDS": 60,
            "OTP_MAX_REQUESTS_PER_WINDOW": 5,
            "OTP_KEY_PREFIX": otp_prefix,
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

        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture()
def client(
    app,
):
    return app.test_client()


def _login(
    client,
    *,
    phone="9876543280",
):
    request_response = client.post(
        "/api/v1/auth/request-otp",
        json={
            "phone": phone,
        },
    )

    assert (
        request_response.status_code
        == 200
    )

    verify_response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "phone": phone,
            "otp": "654321",
        },
    )

    assert (
        verify_response.status_code
        == 200
    )

    payload = (
        verify_response.get_json()
    )

    return {
        "payload": payload,
        "user_id": ObjectId(
            payload[
                "user"
            ][
                "id"
            ]
        ),
        "headers": {
            "Authorization": (
                "Bearer "
                + payload[
                    "access_token"
                ]
            )
        },
    }


def _conversation_payload(
    *,
    conversation_id=None,
    match_id=None,
    other_user_id=None,
):
    return {
        "id": str(
            conversation_id
            or ObjectId()
        ),
        "match_id": str(
            match_id
            or ObjectId()
        ),
        "status": "active",
        "other_user_id": str(
            other_user_id
            or ObjectId()
        ),
        "unread_count": 0,
        "last_read_sequence": 0,
        "last_delivered_sequence": 0,
        "last_sequence": 0,
        "last_message_id": None,
        "last_message_at": None,
        "last_message_preview": None,
        "created_at": None,
        "updated_at": None,
    }


def _message_payload(
    *,
    conversation_id=None,
    match_id=None,
    sender_id=None,
    recipient_id=None,
    sequence=1,
):
    return {
        "id": str(
            ObjectId()
        ),
        "conversation_id": str(
            conversation_id
            or ObjectId()
        ),
        "match_id": str(
            match_id
            or ObjectId()
        ),
        "sender_id": str(
            sender_id
            or ObjectId()
        ),
        "recipient_id": str(
            recipient_id
            or ObjectId()
        ),
        "sequence": sequence,
        "client_message_id": (
            "mobile-message-001"
        ),
        "message_type": "text",
        "body": "Hello Xuoroni",
        "delivery_status": "sent",
        "sent_at": None,
        "delivered_at": None,
        "read_at": None,
        "moderation_status": (
            "not_reviewed"
        ),
        "deleted_at": None,
    }


def test_chat_api_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/chats"
    )

    assert (
        response.status_code
        == 401
    )

    body = response.get_json()

    assert (
        body[
            "code"
        ]
        == "AUTHENTICATION_REQUIRED"
    )

    assert (
        body[
            "message"
        ]
        == (
            "A valid access token "
            "is required."
        )
    )

    assert isinstance(
        body.get(
            "request_id"
        ),
        str,
    )

    assert body[
        "request_id"
    ]


def test_create_chat_uses_authenticated_user_and_returns_201(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    match_id = ObjectId()

    expected = {
        "created": True,
        "conversation": (
            _conversation_payload(
                match_id=match_id
            )
        ),
    }

    captured = {}

    def fake_ensure(
        *,
        user_id,
        match_id,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "match_id"
        ] = match_id

        return expected

    monkeypatch.setattr(
        chat_routes,
        "ensure_chat_for_match",
        fake_ensure,
    )

    response = client.post(
        (
            "/api/v1/chats/matches/"
            + str(match_id)
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 201
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "match_id"
        ]
        == str(
            match_id
        )
    )


def test_create_existing_chat_returns_200(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    match_id = ObjectId()

    expected = {
        "created": False,
        "conversation": (
            _conversation_payload(
                match_id=match_id
            )
        ),
    }

    monkeypatch.setattr(
        chat_routes,
        "ensure_chat_for_match",
        lambda **kwargs: expected,
    )

    response = client.post(
        (
            "/api/v1/chats/matches/"
            + str(match_id)
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )


def test_list_chats_forwards_authenticated_identity_and_query(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    captured = {}

    expected = {
        "conversations": [],
        "returned": 0,
    }

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

        return expected

    monkeypatch.setattr(
        chat_routes,
        "list_my_conversations",
        fake_list,
    )

    response = client.get(
        (
            "/api/v1/chats"
            "?status=closed"
            "&limit=25"
            "&user_id="
            + str(ObjectId())
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "status"
        ]
        == "closed"
    )

    assert (
        captured[
            "limit"
        ]
        == "25"
    )


def test_get_chat_uses_authenticated_identity(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    expected = (
        _conversation_payload(
            conversation_id=(
                conversation_id
            )
        )
    )

    captured = {}

    def fake_get(
        *,
        user_id,
        conversation_id,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "conversation_id"
        ] = conversation_id

        return expected

    monkeypatch.setattr(
        chat_routes,
        "get_my_conversation",
        fake_get,
    )

    response = client.get(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "conversation_id"
        ]
        == str(
            conversation_id
        )
    )


def test_message_history_forwards_cursor_and_limit(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    expected = {
        "conversation": (
            _conversation_payload(
                conversation_id=(
                    conversation_id
                )
            )
        ),
        "messages": [],
        "has_more": False,
        "next_before_sequence": None,
    }

    captured = {}

    def fake_messages(
        *,
        user_id,
        conversation_id,
        before_sequence,
        limit,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "conversation_id"
        ] = conversation_id

        captured[
            "before_sequence"
        ] = before_sequence

        captured[
            "limit"
        ] = limit

        return expected

    monkeypatch.setattr(
        chat_routes,
        "list_my_messages",
        fake_messages,
    )

    response = client.get(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/messages"
            "?before_sequence=90"
            "&limit=30"
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "before_sequence"
        ]
        == "90"
    )

    assert (
        captured[
            "limit"
        ]
        == "30"
    )


def test_send_message_returns_201_for_new_message(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    expected = {
        "created": True,
        "message": (
            _message_payload(
                conversation_id=(
                    conversation_id
                ),
                sender_id=(
                    login[
                        "user_id"
                    ]
                ),
            )
        ),
    }

    captured = {}

    def fake_send(
        *,
        user_id,
        conversation_id,
        client_message_id,
        body,
        message_type,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "conversation_id"
        ] = conversation_id

        captured[
            "client_message_id"
        ] = client_message_id

        captured[
            "body"
        ] = body

        captured[
            "message_type"
        ] = message_type

        return expected

    monkeypatch.setattr(
        chat_routes,
        "send_chat_message",
        fake_send,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/messages"
        ),
        headers=login[
            "headers"
        ],
        json={
            "client_message_id": (
                "mobile-message-001"
            ),
            "body": "Hello Xuoroni",
            "message_type": "text",
            "user_id": str(
                ObjectId()
            ),
        },
    )

    assert (
        response.status_code
        == 201
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "conversation_id"
        ]
        == str(
            conversation_id
        )
    )

    assert (
        captured[
            "client_message_id"
        ]
        == "mobile-message-001"
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


def test_idempotent_message_retry_returns_200(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    expected = {
        "created": False,
        "message": (
            _message_payload(
                conversation_id=(
                    conversation_id
                )
            )
        ),
    }

    monkeypatch.setattr(
        chat_routes,
        "send_chat_message",
        lambda **kwargs: expected,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/messages"
        ),
        headers=login[
            "headers"
        ],
        json={
            "client_message_id": (
                "mobile-message-001"
            ),
            "body": "Hello Xuoroni",
        },
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {
            "json": [],
        },
        {
            "json": "hello",
        },
    ],
)
def test_send_message_requires_json_object(
    client,
    monkeypatch,
    request_kwargs,
):
    login = _login(
        client
    )

    called = {
        "value": False,
    }

    def fake_send(
        **kwargs,
    ):
        called[
            "value"
        ] = True

        return {
            "created": True,
            "message": {},
        }

    monkeypatch.setattr(
        chat_routes,
        "send_chat_message",
        fake_send,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(ObjectId())
            + "/messages"
        ),
        headers=login[
            "headers"
        ],
        **request_kwargs,
    )

    assert (
        response.status_code
        == 400
    )

    assert response.get_json() == {
        "error": {
            "code": (
                "CHAT_VALIDATION_ERROR"
            ),
            "message": (
                "A JSON request body "
                "is required."
            ),
        }
    }

    assert (
        called[
            "value"
        ]
        is False
    )


def test_chat_service_error_is_translated(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    def unavailable(
        user_id,
        *,
        status,
        limit,
    ):
        raise ChatNotFoundError(
            "CONVERSATION_NOT_FOUND",
            (
                "Conversation was "
                "not found."
            ),
            status_code=404,
        )

    monkeypatch.setattr(
        chat_routes,
        "list_my_conversations",
        unavailable,
    )

    response = client.get(
        "/api/v1/chats",
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 404
    )

    assert response.get_json() == {
        "error": {
            "code": (
                "CONVERSATION_NOT_FOUND"
            ),
            "message": (
                "Conversation was "
                "not found."
            ),
        }
    }


def test_send_conflict_is_translated(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    def conflict(
        **kwargs,
    ):
        raise ChatConflictError(
            (
                "MESSAGE_IDEMPOTENCY_"
                "CONFLICT"
            ),
            (
                "client_message_id "
                "was already used for "
                "a different message."
            ),
            status_code=409,
        )

    monkeypatch.setattr(
        chat_routes,
        "send_chat_message",
        conflict,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(ObjectId())
            + "/messages"
        ),
        headers=login[
            "headers"
        ],
        json={
            "client_message_id": "dup-1",
            "body": "Hello",
        },
    )

    assert (
        response.status_code
        == 409
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == (
            "MESSAGE_IDEMPOTENCY_"
            "CONFLICT"
        )
    )


def test_delivered_endpoint_accepts_empty_body(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    captured = {}

    expected = {
        "conversation": (
            _conversation_payload(
                conversation_id=(
                    conversation_id
                )
            )
        ),
        "through_sequence": 7,
        "modified_count": 2,
    }

    def fake_delivered(
        *,
        user_id,
        conversation_id,
        through_sequence,
    ):
        captured[
            "user_id"
        ] = user_id

        captured[
            "conversation_id"
        ] = conversation_id

        captured[
            "through_sequence"
        ] = through_sequence

        return expected

    monkeypatch.setattr(
        chat_routes,
        "mark_chat_delivered",
        fake_delivered,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/delivered"
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.get_json()
        == expected
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "through_sequence"
        ]
        is None
    )


def test_delivered_endpoint_forwards_sequence(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    captured = {}

    def fake_delivered(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return {
            "conversation": (
                _conversation_payload(
                    conversation_id=(
                        conversation_id
                    )
                )
            ),
            "through_sequence": 12,
            "modified_count": 4,
        }

    monkeypatch.setattr(
        chat_routes,
        "mark_chat_delivered",
        fake_delivered,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/delivered"
        ),
        headers=login[
            "headers"
        ],
        json={
            "through_sequence": 12,
        },
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        captured[
            "through_sequence"
        ]
        == 12
    )


def test_read_endpoint_forwards_sequence(
    client,
    monkeypatch,
):
    login = _login(
        client
    )

    conversation_id = ObjectId()

    captured = {}

    def fake_read(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return {
            "conversation": (
                _conversation_payload(
                    conversation_id=(
                        conversation_id
                    )
                )
            ),
            "through_sequence": 15,
            "modified_count": 5,
        }

    monkeypatch.setattr(
        chat_routes,
        "mark_chat_read",
        fake_read,
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(
                conversation_id
            )
            + "/read"
        ),
        headers=login[
            "headers"
        ],
        json={
            "through_sequence": 15,
        },
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        captured[
            "user_id"
        ]
        == login[
            "user_id"
        ]
    )

    assert (
        captured[
            "through_sequence"
        ]
        == 15
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "delivered",
        "read",
    ],
)
def test_receipt_endpoint_rejects_non_object_json(
    client,
    suffix,
):
    login = _login(
        client
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(ObjectId())
            + "/"
            + suffix
        ),
        headers=login[
            "headers"
        ],
        json=[
            1,
            2,
        ],
    )

    assert (
        response.status_code
        == 400
    )

    assert response.get_json() == {
        "error": {
            "code": (
                "CHAT_VALIDATION_ERROR"
            ),
            "message": (
                "Request body must "
                "be a JSON object."
            ),
        }
    }


def test_receipt_endpoint_rejects_malformed_json(
    client,
):
    login = _login(
        client
    )

    response = client.post(
        (
            "/api/v1/chats/"
            + str(ObjectId())
            + "/read"
        ),
        headers=login[
            "headers"
        ],
        data="{bad json",
        content_type="application/json",
    )

    assert (
        response.status_code
        == 400
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "CHAT_VALIDATION_ERROR"
    )


def test_chat_routes_enforce_http_methods(
    client,
):
    response = client.delete(
        "/api/v1/chats"
    )

    assert (
        response.status_code
        == 405
    )

    response = client.get(
        (
            "/api/v1/chats/matches/"
            + str(ObjectId())
        )
    )

    assert (
        response.status_code
        == 405
    )