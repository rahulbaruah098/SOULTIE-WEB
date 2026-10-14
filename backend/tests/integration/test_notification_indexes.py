"""MongoDB integration tests for Xuoroni Notification indexes."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo


TEST_DATABASE = (
    "xuoroni_test_notification_indexes"
)

BASE_TIME = datetime(
    2026,
    10,
    14,
    12,
    0,
    tzinfo=timezone.utc,
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
def clean_notification_state(
    app,
):
    with app.app_context():
        mongo.db.notifications.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notifications.delete_many(
            {}
        )


def _notification(
    *,
    user_id=None,
    dedupe_key_marker=True,
    dedupe_key=None,
):
    document = {
        "schema_version": 1,
        "user_id": (
            user_id
            or ObjectId()
        ),
        "type": "security_alert",
        "title": "Account security",
        "body": (
            "An account security "
            "update is available."
        ),
        "resource_type": None,
        "resource_id": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "read_at": None,
        "dismissed_at": None,
    }

    if dedupe_key_marker:
        document[
            "dedupe_key"
        ] = dedupe_key

    return document


def test_notification_indexes_are_created(
    app,
):
    with app.app_context():
        information = (
            mongo.db.notifications
            .index_information()
        )

    listing_indexes = []

    for name, definition in (
        information.items()
    ):
        keys = definition.get(
            "key"
        )

        if keys == [
            ("user_id", 1),
            ("read_at", 1),
            ("created_at", -1),
        ]:
            listing_indexes.append(
                name
            )

    assert len(
        listing_indexes
    ) == 1

    assert (
        "notification_user_dedupe_unique"
        in information
    )

    dedupe = information[
        "notification_user_dedupe_unique"
    ]

    assert dedupe[
        "key"
    ] == [
        ("user_id", 1),
        ("dedupe_key", 1),
    ]

    assert (
        dedupe.get(
            "unique"
        )
        is True
    )

    assert dedupe[
        "partialFilterExpression"
    ] == {
        "dedupe_key": {
            "$type": "string",
        }
    }


def test_same_recipient_and_dedupe_key_is_unique(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key=(
                    "security:"
                    "same-event"
                ),
            )
        )

        with pytest.raises(
            DuplicateKeyError
        ):
            mongo.db.notifications.insert_one(
                _notification(
                    user_id=user_id,
                    dedupe_key=(
                        "security:"
                        "same-event"
                    ),
                )
            )


def test_same_dedupe_key_is_allowed_for_different_recipients(
    app,
):
    dedupe_key = (
        "system:"
        "announcement-001"
    )

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=ObjectId(),
                dedupe_key=dedupe_key,
            )
        )

        mongo.db.notifications.insert_one(
            _notification(
                user_id=ObjectId(),
                dedupe_key=dedupe_key,
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "dedupe_key": dedupe_key,
                }
            )
            == 2
        )


def test_different_dedupe_keys_are_allowed_for_same_recipient(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key="event:first",
            )
        )

        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key="event:second",
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 2
        )


def test_notifications_with_null_dedupe_key_are_not_unique(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key=None,
            )
        )

        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key=None,
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                    "dedupe_key": None,
                }
            )
            == 2
        )


def test_notifications_without_dedupe_key_are_not_unique(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key_marker=False,
            )
        )

        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key_marker=False,
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 2
        )


def test_non_string_dedupe_key_does_not_participate_in_unique_index(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key=123,
            )
        )

        mongo.db.notifications.insert_one(
            _notification(
                user_id=user_id,
                dedupe_key=123,
            )
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                    "dedupe_key": 123,
                }
            )
            == 2
        )
