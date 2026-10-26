"""Integration tests for Soultie realtime Chat receipts."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import (
    mongo,
    socketio,
)
import app.realtime.chat as realtime_chat
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_chat_receipts"
)


RECEIPT_CASES = [
    (
        "message.delivered",
        "mark_chat_delivered",
    ),
    (
        "message.read",
        "mark_chat_read",
    ),
]


class _ReceiptServiceError(
    RuntimeError
):
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
        self.status_code = (
            status_code
        )


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "SECRET_KEY": (
                "xuoroni-realtime-receipt-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-realtime-receipt-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
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


def _create_user():
    user_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    return mongo.db.users.find_one(
        {
            "_id": user_id,
        }
    )


def _issue(
    user,
):
    return issue_session(
        user[
            "_id"
        ],
        metadata={
            "source": (
                "realtime-chat-receipt-test"
            ),
        },
    )


def _connect(
    app,
    tokens,
):
    return socketio.test_client(
        app,
        namespace="/",
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )


def _conversation_payload(
    conversation_id,
):
    return {
        "id": str(
            conversation_id
        ),
        "match_id": str(
            ObjectId()
        ),
        "status": "active",
        "other_user_id": str(
            ObjectId()
        ),
        "unread_count": 0,
        "last_read_sequence": 0,
        "last_delivered_sequence": 0,
        "last_sequence": 3,
        "last_message_id": str(
            ObjectId()
        ),
        "last_message_at": (
            "2026-10-14T00:00:00+00:00"
        ),
        "last_message_preview": "Hello",
        "created_at": (
            "2026-10-14T00:00:00+00:00"
        ),
        "updated_at": (
            "2026-10-14T00:00:00+00:00"
        ),
    }


def _install_room_authorizer(
    monkeypatch,
    *,
    conversation_id,
    allowed_user_ids,
):
    allowed = {
        str(
            user_id
        )
        for user_id
        in allowed_user_ids
    }

    expected_conversation_id = str(
        conversation_id
    )

    def fake_get_my_conversation(
        *,
        user_id,
        conversation_id,
    ):
        if (
            str(
                user_id
            )
            not in allowed
            or str(
                conversation_id
            )
            != expected_conversation_id
        ):
            raise AssertionError(
                (
                    "Unexpected room "
                    "authorization request."
                )
            )

        return _conversation_payload(
            expected_conversation_id
        )

    monkeypatch.setattr(
        realtime_chat,
        "get_my_conversation",
        fake_get_my_conversation,
    )


def _join(
    client,
    conversation_id,
):
    response = client.emit(
        "chat:join",
        {
            "conversation_id": str(
                conversation_id
            ),
        },
        namespace="/",
        callback=True,
    )

    assert response[
        "ok"
    ] is True


def _events(
    client,
    event_name,
):
    return [
        packet
        for packet
        in client.get_received(
            namespace="/"
        )
        if packet.get(
            "name"
        )
        == event_name
    ]


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
def test_receipt_broadcasts_only_to_joined_conversation_room(
    app,
    monkeypatch,
    event_name,
    service_name,
):
    with app.app_context():
        actor = _create_user()
        peer = _create_user()
        outsider = _create_user()

        actor_tokens = _issue(
            actor
        )

        peer_tokens = _issue(
            peer
        )

        outsider_tokens = _issue(
            outsider
        )

    conversation_id = ObjectId()

    _install_room_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
            peer[
                "_id"
            ],
        },
    )

    calls = []

    def fake_receipt(
        *,
        user_id,
        conversation_id,
        through_sequence,
    ):
        calls.append(
            {
                "user_id": str(
                    user_id
                ),
                "conversation_id": str(
                    conversation_id
                ),
                "through_sequence": (
                    through_sequence
                ),
            }
        )

        return {
            "conversation": (
                _conversation_payload(
                    conversation_id
                )
            ),
            "through_sequence": 3,
            "modified_count": 2,
        }

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        fake_receipt,
    )

    actor_client = _connect(
        app,
        actor_tokens,
    )

    peer_client = _connect(
        app,
        peer_tokens,
    )

    outsider_client = _connect(
        app,
        outsider_tokens,
    )

    try:
        _join(
            actor_client,
            conversation_id,
        )

        _join(
            peer_client,
            conversation_id,
        )

        response = actor_client.emit(
            event_name,
            {
                "conversation_id": str(
                    conversation_id
                ),
                "through_sequence": "3",
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert response[
            "through_sequence"
        ] == 3

        assert response[
            "modified_count"
        ] == 2

        assert len(
            calls
        ) == 1

        assert (
            calls[
                0
            ][
                "user_id"
            ]
            == str(
                actor[
                    "_id"
                ]
            )
        )

        assert (
            calls[
                0
            ][
                "conversation_id"
            ]
            == str(
                conversation_id
            )
        )

        assert (
            calls[
                0
            ][
                "through_sequence"
            ]
            == "3"
        )

        actor_events = _events(
            actor_client,
            event_name,
        )

        peer_events = _events(
            peer_client,
            event_name,
        )

        outsider_events = _events(
            outsider_client,
            event_name,
        )

        assert len(
            actor_events
        ) == 1

        assert len(
            peer_events
        ) == 1

        assert (
            outsider_events
            == []
        )

        actor_payload = (
            actor_events[
                0
            ][
                "args"
            ][
                0
            ]
        )

        peer_payload = (
            peer_events[
                0
            ][
                "args"
            ][
                0
            ]
        )

        expected_payload = {
            "conversation_id": str(
                conversation_id
            ),
            "through_sequence": 3,
            "modified_count": 2,
            "user_id": str(
                actor[
                    "_id"
                ]
            ),
        }

        assert (
            actor_payload
            == expected_payload
        )

        assert (
            peer_payload
            == expected_payload
        )

    finally:
        for client in (
            actor_client,
            peer_client,
            outsider_client,
        ):
            if client.is_connected(
                "/"
            ):
                client.disconnect(
                    namespace="/"
                )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
def test_client_cannot_forge_receipt_identity(
    app,
    monkeypatch,
    event_name,
    service_name,
):
    with app.app_context():
        actor = _create_user()
        victim = _create_user()

        tokens = _issue(
            actor
        )

    conversation_id = ObjectId()

    captured = {}

    def fake_receipt(
        *,
        user_id,
        conversation_id,
        through_sequence,
    ):
        captured.update(
            {
                "user_id": str(
                    user_id
                ),
                "conversation_id": str(
                    conversation_id
                ),
                "through_sequence": (
                    through_sequence
                ),
            }
        )

        return {
            "through_sequence": 4,
            "modified_count": 0,
        }

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        fake_receipt,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            event_name,
            {
                "conversation_id": str(
                    conversation_id
                ),
                "through_sequence": "4",
                "user_id": str(
                    victim[
                        "_id"
                    ]
                ),
                "sender_id": str(
                    victim[
                        "_id"
                    ]
                ),
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert (
            captured[
                "user_id"
            ]
            == str(
                actor[
                    "_id"
                ]
            )
        )

        assert (
            captured[
                "user_id"
            ]
            != str(
                victim[
                    "_id"
                ]
            )
        )

        assert (
            captured[
                "through_sequence"
            ]
            == "4"
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
def test_revoked_session_cannot_update_receipt(
    app,
    monkeypatch,
    event_name,
    service_name,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    calls = []

    def must_not_run(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        raise AssertionError(
            (
                "Receipt service must not "
                "run after session revocation."
            )
        )

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        must_not_run,
    )

    client = _connect(
        app,
        tokens,
    )

    assert client.is_connected(
        "/"
    )

    with app.app_context():
        mongo.db.auth_sessions.update_one(
            {
                "session_id": (
                    tokens[
                        "session_id"
                    ]
                ),
            },
            {
                "$set": {
                    "revoked_at": (
                        datetime.now(
                            timezone.utc
                        )
                    ),
                    "revoke_reason": (
                        "realtime-receipt-test"
                    ),
                },
            },
        )

    try:
        response = client.emit(
            event_name,
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "through_sequence": 3,
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "INVALID_ACCESS_TOKEN"
        )

        assert calls == []

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
def test_duplicate_receipt_ack_does_not_rebroadcast(
    app,
    monkeypatch,
    event_name,
    service_name,
):
    with app.app_context():
        actor = _create_user()
        peer = _create_user()

        actor_tokens = _issue(
            actor
        )

        peer_tokens = _issue(
            peer
        )

    conversation_id = ObjectId()

    _install_room_authorizer(
        monkeypatch,
        conversation_id=conversation_id,
        allowed_user_ids={
            actor[
                "_id"
            ],
            peer[
                "_id"
            ],
        },
    )

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        lambda **_kwargs: {
            "through_sequence": 3,
            "modified_count": 0,
        },
    )

    actor_client = _connect(
        app,
        actor_tokens,
    )

    peer_client = _connect(
        app,
        peer_tokens,
    )

    try:
        _join(
            actor_client,
            conversation_id,
        )

        _join(
            peer_client,
            conversation_id,
        )

        response = actor_client.emit(
            event_name,
            {
                "conversation_id": str(
                    conversation_id
                ),
                "through_sequence": 3,
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert response[
            "through_sequence"
        ] == 3

        assert response[
            "modified_count"
        ] == 0

        assert (
            _events(
                actor_client,
                event_name,
            )
            == []
        )

        assert (
            _events(
                peer_client,
                event_name,
            )
            == []
        )

    finally:
        if actor_client.is_connected(
            "/"
        ):
            actor_client.disconnect(
                namespace="/"
            )

        if peer_client.is_connected(
            "/"
        ):
            peer_client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
def test_receipt_service_error_is_returned_as_safe_ack(
    app,
    monkeypatch,
    event_name,
    service_name,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    monkeypatch.setattr(
        realtime_chat,
        "ChatServiceError",
        _ReceiptServiceError,
    )

    def reject_receipt(
        **_kwargs,
    ):
        raise _ReceiptServiceError(
            "INVALID_RECEIPT_SEQUENCE",
            (
                "Receipt sequence "
                "is invalid."
            ),
            status_code=400,
        )

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        reject_receipt,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            event_name,
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "through_sequence": 0,
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "INVALID_RECEIPT_SEQUENCE"
        )

        assert (
            response[
                "error"
            ][
                "message"
            ]
            == (
                "Receipt sequence "
                "is invalid."
            )
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {
            "conversation_id": "",
        },
    ],
)
def test_invalid_receipt_event_payload_is_rejected_before_service(
    app,
    monkeypatch,
    event_name,
    service_name,
    payload,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    calls = []

    def must_not_run(
        **kwargs,
    ):
        calls.append(
            kwargs
        )

        raise AssertionError(
            (
                "Receipt service must not "
                "run for invalid payload."
            )
        )

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        must_not_run,
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            event_name,
            payload,
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CHAT_VALIDATION_ERROR"
        )

        assert calls == []

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )


@pytest.mark.parametrize(
    (
        "event_name",
        "service_name",
    ),
    RECEIPT_CASES,
)
@pytest.mark.parametrize(
    "invalid_result",
    [
        None,
        {},
        {
            "through_sequence": 3,
        },
        {
            "through_sequence": True,
            "modified_count": 1,
        },
    ],
)
def test_invalid_receipt_service_state_is_not_broadcast(
    app,
    monkeypatch,
    event_name,
    service_name,
    invalid_result,
):
    with app.app_context():
        user = _create_user()
        tokens = _issue(
            user
        )

    monkeypatch.setattr(
        realtime_chat,
        service_name,
        lambda **_kwargs: (
            invalid_result
        ),
    )

    client = _connect(
        app,
        tokens,
    )

    try:
        response = client.emit(
            event_name,
            {
                "conversation_id": str(
                    ObjectId()
                ),
                "through_sequence": 3,
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is False

        assert (
            response[
                "error"
            ][
                "code"
            ]
            == "CHAT_STATE_INVALID"
        )

        assert (
            _events(
                client,
                event_name,
            )
            == []
        )

    finally:
        if client.is_connected(
            "/"
        ):
            client.disconnect(
                namespace="/"
            )