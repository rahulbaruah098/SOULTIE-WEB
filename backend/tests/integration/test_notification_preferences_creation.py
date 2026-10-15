"""Integration tests for Notification Preference enforcement during creation."""

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
from app.schemas.notification_preferences import (
    MANDATORY_IN_APP_NOTIFICATION_TYPES,
)
from app.services.notification_service import (
    NotificationLifecycleError,
    create_notification,
    update_my_notification_preferences,
)


TEST_DATABASE = (
    "xuoroni_test_notification_preferences_creation"
)


BASE_TIME = datetime(
    2026,
    10,
    15,
    16,
    30,
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
def clean_state(
    app,
):
    with app.app_context():
        mongo.db.notifications.delete_many(
            {}
        )

        mongo.db.notification_preferences.delete_many(
            {}
        )

        mongo.db.users.delete_many(
            {}
        )

        mongo.db.matches.delete_many(
            {}
        )

        mongo.db.conversations.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notifications.delete_many(
            {}
        )

        mongo.db.notification_preferences.delete_many(
            {}
        )

        mongo.db.users.delete_many(
            {}
        )

        mongo.db.matches.delete_many(
            {}
        )

        mongo.db.conversations.delete_many(
            {}
        )


def _create_active_user():
    user_id = ObjectId()

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": "active",
        }
    )

    return user_id


def test_disabled_optional_notification_is_not_persisted(
    app,
):
    with app.app_context():
        user_id = _create_active_user()

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    "system_announcement",
                ],
            },
            now=BASE_TIME,
        )

        result = create_notification(
            user_id=user_id,
            notification_type=(
                "system_announcement"
            ),
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        assert result == {
            "notification": None,
            "created": False,
        }

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )


def test_missing_preferences_preserve_default_enabled_creation(
    app,
):
    with app.app_context():
        user_id = _create_active_user()

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )

        result = create_notification(
            user_id=user_id,
            notification_type=(
                "system_announcement"
            ),
            now=BASE_TIME,
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        assert (
            result[
                "notification"
            ][
                "type"
            ]
            == "system_announcement"
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )


@pytest.mark.parametrize(
    "notification_type",
    sorted(
        MANDATORY_IN_APP_NOTIFICATION_TYPES
    ),
)
def test_mandatory_notifications_are_never_suppressed(
    app,
    notification_type,
):
    with app.app_context():
        user_id = _create_active_user()

        # Store a real preference record that disables an optional type.
        # Mandatory types must remain creation-eligible regardless.
        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    "system_announcement",
                ],
            },
            now=BASE_TIME,
        )

        result = create_notification(
            user_id=user_id,
            notification_type=(
                notification_type
            ),
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        assert (
            result[
                "created"
            ]
            is True
        )

        assert (
            result[
                "notification"
            ][
                "type"
            ]
            == notification_type
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                    "type": notification_type,
                }
            )
            == 1
        )


def test_suppressed_attempt_does_not_consume_dedupe_key(
    app,
):
    with app.app_context():
        user_id = _create_active_user()

        dedupe_key = (
            "system.preference:permanent-test"
        )

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    "system_announcement",
                ],
            },
            now=BASE_TIME,
        )

        suppressed = create_notification(
            user_id=user_id,
            notification_type=(
                "system_announcement"
            ),
            dedupe_key=dedupe_key,
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        assert suppressed == {
            "notification": None,
            "created": False,
        }

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                    "dedupe_key": dedupe_key,
                }
            )
            == 0
        )


def test_reenabled_notification_can_be_created_after_suppression(
    app,
):
    with app.app_context():
        user_id = _create_active_user()

        dedupe_key = (
            "system.preference:reenable-test"
        )

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    "system_announcement",
                ],
            },
            now=BASE_TIME,
        )

        first = create_notification(
            user_id=user_id,
            notification_type=(
                "system_announcement"
            ),
            dedupe_key=dedupe_key,
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        assert first == {
            "notification": None,
            "created": False,
        }

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [],
            },
            now=(
                BASE_TIME
                + timedelta(
                    seconds=2
                )
            ),
        )

        second = create_notification(
            user_id=user_id,
            notification_type=(
                "system_announcement"
            ),
            dedupe_key=dedupe_key,
            now=(
                BASE_TIME
                + timedelta(
                    seconds=3
                )
            ),
        )

        assert (
            second[
                "created"
            ]
            is True
        )

        assert (
            second[
                "notification"
            ][
                "type"
            ]
            == "system_announcement"
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                    "dedupe_key": dedupe_key,
                }
            )
            == 1
        )


def test_resource_lifecycle_validation_precedes_preference_suppression(
    app,
):
    with app.app_context():
        user_id = _create_active_user()

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    "new_match",
                ],
            },
            now=BASE_TIME,
        )

        unavailable_match_id = ObjectId()

        with pytest.raises(
            NotificationLifecycleError
        ) as exc_info:
            create_notification(
                user_id=user_id,
                notification_type=(
                    "new_match"
                ),
                resource_type="match",
                resource_id=(
                    unavailable_match_id
                ),
                dedupe_key=(
                    "match.preference:"
                    + str(
                        unavailable_match_id
                    )
                ),
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=1
                    )
                ),
            )

        assert (
            exc_info.value.code
            == "NOTIFICATION_RESOURCE_UNAVAILABLE"
        )

        assert (
            exc_info.value.status_code
            == 409
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )
