"""Permanent integration tests for durable new-message Notifications."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.notification_preferences_repository import (
    update_notification_preferences_for_user,
)
from app.services import chat_service
from app.services.chat_service import (
    ensure_chat_for_match,
    send_chat_message,
)


TEST_DATABASE = (
    "xuoroni_test_notification_new_message_producer"
)


@pytest.fixture(
    scope="module"
)
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


@pytest.fixture(
    autouse=True
)
def clean_database(
    app,
):
    with app.app_context():
        for collection_name in (
            "notification_preferences",
            "notifications",
            "messages",
            "conversations",
            "matches",
            "profiles",
            "users",
        ):
            mongo.db[
                collection_name
            ].delete_many(
                {}
            )

    yield


def _create_ready_user():
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

    mongo.db.profiles.insert_one(
        {
            "_id": ObjectId(),
            "user_id": user_id,
            "onboarding_status": "completed",
            "profile_status": "active",
            "visibility": "visible",
            "created_at": now,
            "updated_at": now,
        }
    )

    return user_id


def _prepare_conversation():
    sender_id = (
        _create_ready_user()
    )

    recipient_id = (
        _create_ready_user()
    )

    match_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    mongo.db.matches.insert_one(
        {
            "_id": match_id,
            "user_ids": [
                sender_id,
                recipient_id,
            ],
            "status": "active",
            "matched_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )

    result = ensure_chat_for_match(
        user_id=sender_id,
        match_id=match_id,
    )

    return {
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "match_id": match_id,
        "conversation_id": (
            result[
                "conversation"
            ][
                "id"
            ]
        ),
    }


def test_new_message_creates_recipient_only_notification(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

        secret_body = (
            "PRIVATE CHAT CONTENT "
            "MUST NOT ENTER NOTIFICATION"
        )

        result = send_chat_message(
            user_id=state[
                "sender_id"
            ],
            conversation_id=state[
                "conversation_id"
            ],
            client_message_id=(
                "producer-test-001"
            ),
            body=secret_body,
            message_type="text",
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        message_id = (
            result[
                "message"
            ][
                "id"
            ]
        )

        conversation_id = ObjectId(
            state[
                "conversation_id"
            ]
        )

        notifications = list(
            mongo.db.notifications.find(
                {
                    "type": "new_message",
                    "resource_type": (
                        "conversation"
                    ),
                    "resource_id": (
                        conversation_id
                    ),
                }
            )
        )

        assert (
            len(
                notifications
            )
            == 1
        )

        notification = (
            notifications[
                0
            ]
        )

        assert (
            notification[
                "user_id"
            ]
            == state[
                "recipient_id"
            ]
        )

        assert (
            notification[
                "user_id"
            ]
            != state[
                "sender_id"
            ]
        )

        assert (
            notification[
                "dedupe_key"
            ]
            == (
                "new_message:"
                + message_id
            )
        )

        assert (
            secret_body
            not in repr(
                notification
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": state[
                        "sender_id"
                    ],
                    "type": "new_message",
                }
            )
            == 0
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {}
            )
            == 0
        )


def test_idempotent_retry_does_not_duplicate_notification(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

        first = send_chat_message(
            user_id=state[
                "sender_id"
            ],
            conversation_id=state[
                "conversation_id"
            ],
            client_message_id=(
                "producer-test-002"
            ),
            body="Hello",
            message_type="text",
        )

        retry = send_chat_message(
            user_id=state[
                "sender_id"
            ],
            conversation_id=state[
                "conversation_id"
            ],
            client_message_id=(
                "producer-test-002"
            ),
            body="Hello",
            message_type="text",
        )

        assert (
            first[
                "created"
            ]
            is True
        )

        assert (
            retry[
                "created"
            ]
            is False
        )

        assert (
            retry[
                "message"
            ][
                "id"
            ]
            == first[
                "message"
            ][
                "id"
            ]
        )

        conversation_id = ObjectId(
            state[
                "conversation_id"
            ]
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": state[
                        "recipient_id"
                    ],
                    "type": "new_message",
                    "resource_id": (
                        conversation_id
                    ),
                }
            )
            == 1
        )


def test_disabled_new_message_preference_suppresses_only_notification(
    app,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

        update_notification_preferences_for_user(
            state[
                "recipient_id"
            ],
            disabled_types=[
                "new_message",
            ],
        )

        result = send_chat_message(
            user_id=state[
                "sender_id"
            ],
            conversation_id=state[
                "conversation_id"
            ],
            client_message_id=(
                "producer-test-003"
            ),
            body=(
                "This message must remain durable."
            ),
            message_type="text",
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        conversation_id = ObjectId(
            state[
                "conversation_id"
            ]
        )

        assert (
            mongo.db.messages.count_documents(
                {
                    "conversation_id": (
                        conversation_id
                    ),
                }
            )
            == 1
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": state[
                        "recipient_id"
                    ],
                    "type": "new_message",
                }
            )
            == 0
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": state[
                        "recipient_id"
                    ],
                }
            )
            == 1
        )


def test_notification_failure_does_not_fail_durable_message(
    app,
    monkeypatch,
):
    with app.app_context():
        state = (
            _prepare_conversation()
        )

        calls = []

        def fail_notification(
            **kwargs,
        ):
            calls.append(
                kwargs
            )

            raise RuntimeError(
                "Simulated Notification failure."
            )

        monkeypatch.setattr(
            chat_service,
            "create_notification",
            fail_notification,
        )

        result = send_chat_message(
            user_id=state[
                "sender_id"
            ],
            conversation_id=state[
                "conversation_id"
            ],
            client_message_id=(
                "producer-test-004"
            ),
            body=(
                "Durable message survives "
                "Notification failure."
            ),
            message_type="text",
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        assert (
            len(
                calls
            )
            == 1
        )

        assert (
            calls[
                0
            ][
                "user_id"
            ]
            == state[
                "recipient_id"
            ]
        )

        assert (
            calls[
                0
            ][
                "notification_type"
            ]
            == "new_message"
        )

        assert (
            calls[
                0
            ][
                "resource_type"
            ]
            == "conversation"
        )

        conversation_id = ObjectId(
            state[
                "conversation_id"
            ]
        )

        assert (
            calls[
                0
            ][
                "resource_id"
            ]
            == conversation_id
        )

        assert (
            mongo.db.messages.count_documents(
                {
                    "conversation_id": (
                        conversation_id
                    ),
                }
            )
            == 1
        )

        assert (
            mongo.db.notifications.count_documents(
                {}
            )
            == 0
        )
