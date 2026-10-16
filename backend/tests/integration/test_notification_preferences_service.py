"""Integration tests for Xuoroni Notification Preferences service."""

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
    DISABLEABLE_IN_APP_NOTIFICATION_TYPES,
    MANDATORY_IN_APP_NOTIFICATION_TYPES,
)
from app.services.notification_service import (
    NotificationLifecycleError,
    NotificationValidationError,
    get_my_notification_preferences,
    notification_type_is_enabled_for_user,
    update_my_notification_preferences,
)


TEST_DATABASE = (
    "xuoroni_test_notification_preferences_service"
)


BASE_TIME = datetime(
    2026,
    10,
    15,
    15,
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
def clean_preferences_state(
    app,
):
    with app.app_context():
        mongo.db.notification_preferences.delete_many(
            {}
        )

        mongo.db.users.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notification_preferences.delete_many(
            {}
        )

        mongo.db.users.delete_many(
            {}
        )


def _create_user(
    *,
    account_status="active",
):
    user_id = ObjectId()

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": account_status,
        }
    )

    return user_id


def _optional_types():
    values = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    assert len(
        values
    ) >= 2

    return values


def test_get_notification_preferences_creates_default_record(
    app,
):
    with app.app_context():
        user_id = _create_user()

        result = (
            get_my_notification_preferences(
                user_id
            )
        )

        preferences = result[
            "preferences"
        ]

        assert (
            preferences[
                "schema_version"
            ]
            == 1
        )

        assert (
            preferences[
                "in_app"
            ][
                "disabled_types"
            ]
            == []
        )

        assert set(
            preferences[
                "in_app"
            ][
                "mandatory_types"
            ]
        ) == set(
            MANDATORY_IN_APP_NOTIFICATION_TYPES
        )

        assert (
            "user_id"
            not in preferences
        )

        assert (
            "created_at"
            not in preferences
        )

        assert (
            "updated_at"
            not in preferences
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )


def test_get_notification_preferences_is_idempotent(
    app,
):
    with app.app_context():
        user_id = _create_user()

        first = (
            get_my_notification_preferences(
                user_id
            )
        )

        stored_first = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        second = (
            get_my_notification_preferences(
                str(
                    user_id
                )
            )
        )

        stored_second = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        assert (
            first
            == second
        )

        assert (
            stored_first[
                "_id"
            ]
            == stored_second[
                "_id"
            ]
        )

        assert (
            stored_first[
                "created_at"
            ]
            == stored_second[
                "created_at"
            ]
        )

        assert (
            stored_first[
                "updated_at"
            ]
            == stored_second[
                "updated_at"
            ]
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )


def test_update_notification_preferences_persists_canonical_state(
    app,
):
    optional_type = (
        _optional_types()[0]
    )

    with app.app_context():
        user_id = _create_user()

        initial = (
            get_my_notification_preferences(
                user_id
            )
        )

        stored_before = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        result = (
            update_my_notification_preferences(
                user_id,
                {
                    "disabled_types": [
                        optional_type,
                        f" {optional_type.upper()} ",
                        optional_type,
                    ],
                },
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        assert (
            initial[
                "preferences"
            ][
                "in_app"
            ][
                "disabled_types"
            ]
            == []
        )

        assert (
            result[
                "preferences"
            ][
                "in_app"
            ][
                "disabled_types"
            ]
            == [
                optional_type,
            ]
        )

        stored_after = (
            mongo.db.notification_preferences.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        assert (
            stored_after[
                "_id"
            ]
            == stored_before[
                "_id"
            ]
        )

        assert (
            stored_after[
                "created_at"
            ]
            == stored_before[
                "created_at"
            ]
        )

        assert (
            stored_after[
                "disabled_types"
            ]
            == [
                optional_type,
            ]
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "push_enabled": "false",
        },
        {
            "disabled_types": [
                "security_alert",
            ],
        },
        {
            "disabled_types": [
                "unknown_notification_type",
            ],
        },
    ],
)
def test_notification_preferences_service_validation(
    app,
    payload,
):
    with app.app_context():
        user_id = _create_user()

        with pytest.raises(
            NotificationValidationError
        ) as exc_info:
            update_my_notification_preferences(
                user_id,
                payload,
                now=BASE_TIME,
            )

        assert (
            exc_info.value.code
            == "INVALID_NOTIFICATION_PREFERENCES"
        )

        assert (
            exc_info.value.status_code
            == 400
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )


def test_notification_preferences_enablement(
    app,
):
    optional_type = (
        _optional_types()[0]
    )

    with app.app_context():
        user_id = _create_user()

        update_my_notification_preferences(
            user_id,
            {
                "disabled_types": [
                    optional_type,
                ],
            },
            now=BASE_TIME,
        )

        assert (
            notification_type_is_enabled_for_user(
                user_id,
                optional_type,
            )
            is False
        )

        for mandatory_type in (
            MANDATORY_IN_APP_NOTIFICATION_TYPES
        ):
            assert (
                notification_type_is_enabled_for_user(
                    user_id,
                    mandatory_type,
                )
                is True
            )


def test_missing_preferences_default_enabled_without_write(
    app,
):
    optional_type = (
        _optional_types()[0]
    )

    with app.app_context():
        user_id = _create_user()

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )

        assert (
            notification_type_is_enabled_for_user(
                user_id,
                optional_type,
            )
            is True
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )


def test_unknown_notification_type_maps_to_validation_error(
    app,
):
    with app.app_context():
        user_id = _create_user()

        with pytest.raises(
            NotificationValidationError
        ) as exc_info:
            notification_type_is_enabled_for_user(
                user_id,
                "unknown_notification_type",
            )

        assert (
            exc_info.value.code
            == "INVALID_NOTIFICATION_TYPE"
        )

        assert (
            exc_info.value.status_code
            == 400
        )


@pytest.mark.parametrize(
    "operation",
    [
        "get",
        "update",
        "enablement",
    ],
)
def test_unavailable_account_rejected_by_preferences_service(
    app,
    operation,
):
    optional_type = (
        _optional_types()[0]
    )

    with app.app_context():
        user_id = _create_user(
            account_status="suspended"
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            if operation == "get":
                get_my_notification_preferences(
                    user_id
                )

            elif operation == "update":
                update_my_notification_preferences(
                    user_id,
                    {
                        "disabled_types": [],
                    },
                    now=BASE_TIME,
                )

            else:
                notification_type_is_enabled_for_user(
                    user_id,
                    optional_type,
                )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )


def test_notification_preferences_users_remain_isolated(
    app,
):
    first_type, second_type = (
        _optional_types()[:2]
    )

    with app.app_context():
        first_user = _create_user()
        second_user = _create_user()

        update_my_notification_preferences(
            first_user,
            {
                "disabled_types": [
                    first_type,
                ],
            },
            now=BASE_TIME,
        )

        update_my_notification_preferences(
            second_user,
            {
                "disabled_types": [
                    second_type,
                ],
            },
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        first = (
            get_my_notification_preferences(
                first_user
            )
        )

        second = (
            get_my_notification_preferences(
                second_user
            )
        )

        assert (
            first[
                "preferences"
            ][
                "in_app"
            ][
                "disabled_types"
            ]
            == [
                first_type,
            ]
        )

        assert (
            second[
                "preferences"
            ][
                "in_app"
            ][
                "disabled_types"
            ]
            == [
                second_type,
            ]
        )

        assert (
            notification_type_is_enabled_for_user(
                first_user,
                second_type,
            )
            is True
        )

        assert (
            notification_type_is_enabled_for_user(
                second_user,
                first_type,
            )
            is True
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {}
            )
            == 2
        )
