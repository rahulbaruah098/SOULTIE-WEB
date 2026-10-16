"""Integration tests for Xuoroni Notification delivery orchestration."""

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
from app.repositories.notification_preferences_repository import (
    ensure_notification_preferences_for_user,
    get_notification_preferences_for_user,
    update_notification_preferences_for_user,
)
from app.repositories.user_repository import (
    create_user,
)
from app.schemas.notification import (
    build_notification_document,
)
from app.schemas.notification_preferences import (
    normalize_notification_preferences_patch,
    notification_channel_is_enabled,
    serialize_notification_preferences_for_owner,
)
import app.services.notification_service as notification_service_module
from app.services.notification_service import (
    create_notification,
    update_my_notification_preferences,
)
from app.services.notification_delivery_service import (
    BACKGROUND_NOTIFICATION_DELIVERY_CHANNELS,
    CHANNEL_DISABLED_ERROR_CODE,
    provision_notification_deliveries,
    reconcile_notification_deliveries,
)


TEST_DATABASE = (
    "xuoroni_test_notification_delivery_service"
)


BASE_TIME = datetime(
    2026,
    10,
    16,
    12,
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
        mongo.db.notification_deliveries.delete_many(
            {}
        )
        mongo.db.notifications.delete_many(
            {}
        )
        mongo.db.notification_preferences.delete_many(
            {}
        )
        mongo.db.users.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notification_deliveries.delete_many(
            {}
        )
        mongo.db.notifications.delete_many(
            {}
        )
        mongo.db.notification_preferences.delete_many(
            {}
        )
        mongo.db.users.delete_many(
            {}
        )


def _insert_notification(
    *,
    user_id,
    notification_id=None,
    now=BASE_TIME,
    notification_type="security_alert",
):
    document = build_notification_document(
        user_id=user_id,
        notification_type=notification_type,
        now=now,
    )

    if notification_id is not None:
        document[
            "_id"
        ] = notification_id

    inserted = (
        mongo.db.notifications.insert_one(
            document
        )
    )

    target_id = (
        document.get(
            "_id"
        )
        or inserted.inserted_id
    )

    return mongo.db.notifications.find_one(
        {
            "_id": target_id,
        }
    )


def test_background_channel_policy_defaults_and_patch_validation(
    app,
):
    assert (
        BACKGROUND_NOTIFICATION_DELIVERY_CHANNELS
        == (
            "email",
            "push",
        )
    )

    assert (
        notification_channel_is_enabled(
            None,
            "email",
        )
        is False
    )

    assert (
        notification_channel_is_enabled(
            None,
            "push",
        )
        is True
    )

    assert (
        normalize_notification_preferences_patch(
            {
                "email_enabled": True,
            }
        )
        == {
            "email_enabled": True,
        }
    )

    assert (
        normalize_notification_preferences_patch(
            {
                "push_enabled": False,
            }
        )
        == {
            "push_enabled": False,
        }
    )

    with pytest.raises(
        ValueError
    ):
        normalize_notification_preferences_patch(
            {
                "email_enabled": 1,
            }
        )

    with pytest.raises(
        ValueError
    ):
        notification_channel_is_enabled(
            None,
            "realtime",
        )


def test_channel_preference_partial_updates_preserve_existing_state(
    app,
):
    with app.app_context():
        user = create_user()

        ensure_notification_preferences_for_user(
            user[
                "_id"
            ]
        )

        update_notification_preferences_for_user(
            user[
                "_id"
            ],
            disabled_types=[
                "new_message",
            ],
        )

        update_notification_preferences_for_user(
            user[
                "_id"
            ],
            email_enabled=True,
        )

        update_notification_preferences_for_user(
            user[
                "_id"
            ],
            push_enabled=False,
        )

        current = (
            get_notification_preferences_for_user(
                user[
                    "_id"
                ]
            )
        )

        assert (
            current[
                "disabled_types"
            ]
            == [
                "new_message",
            ]
        )

        assert current[
            "email_enabled"
        ] is True

        assert current[
            "push_enabled"
        ] is False

        update_my_notification_preferences(
            user[
                "_id"
            ],
            {
                "push_enabled": True,
            },
        )

        updated = (
            get_notification_preferences_for_user(
                user[
                    "_id"
                ]
            )
        )

        assert (
            updated[
                "disabled_types"
            ]
            == [
                "new_message",
            ]
        )

        assert updated[
            "email_enabled"
        ] is True

        assert updated[
            "push_enabled"
        ] is True

        serialized = (
            serialize_notification_preferences_for_owner(
                updated
            )
        )

    assert serialized[
        "channels"
    ][
        "email"
    ][
        "enabled"
    ] is True

    assert serialized[
        "channels"
    ][
        "push"
    ][
        "enabled"
    ] is True


def test_old_in_app_only_preference_shape_stays_backward_compatible(
    app,
):
    with app.app_context():
        user = create_user()

        preferences = (
            ensure_notification_preferences_for_user(
                user[
                    "_id"
                ]
            )
        )

        assert (
            "email_enabled"
            not in preferences
        )

        assert (
            "push_enabled"
            not in preferences
        )

        serialized = (
            serialize_notification_preferences_for_owner(
                preferences
            )
        )

    assert (
        "channels"
        not in serialized
    )


def test_delivery_provisioning_creates_email_and_push_only(
    app,
):
    with app.app_context():
        user = create_user()

        notification = _insert_notification(
            user_id=user[
                "_id"
            ],
        )

        result = (
            provision_notification_deliveries(
                notification
            )
        )

        rows = list(
            mongo.db.notification_deliveries.find(
                {
                    "notification_id": (
                        notification[
                            "_id"
                        ]
                    ),
                }
            )
        )

    assert set(
        result[
            "channels"
        ]
    ) == {
        "email",
        "push",
    }

    assert {
        row[
            "channel"
        ]
        for row in rows
    } == {
        "email",
        "push",
    }

    by_channel = {
        row[
            "channel"
        ]: row
        for row in rows
    }

    assert by_channel[
        "email"
    ][
        "status"
    ] == "suppressed"

    assert (
        by_channel[
            "email"
        ][
            "last_error_code"
        ]
        == CHANNEL_DISABLED_ERROR_CODE
    )

    assert by_channel[
        "push"
    ][
        "status"
    ] == "pending"

    assert all(
        row[
            "channel"
        ]
        != "realtime"
        for row in rows
    )


def test_explicit_channel_preferences_control_provisioning(
    app,
):
    with app.app_context():
        user = create_user()

        update_notification_preferences_for_user(
            user[
                "_id"
            ],
            email_enabled=True,
            push_enabled=False,
        )

        notification = _insert_notification(
            user_id=user[
                "_id"
            ],
        )

        provision_notification_deliveries(
            notification
        )

        rows = {
            row[
                "channel"
            ]: row
            for row in (
                mongo.db.notification_deliveries.find(
                    {
                        "notification_id": (
                            notification[
                                "_id"
                            ]
                        ),
                    }
                )
            )
        }

    assert rows[
        "email"
    ][
        "status"
    ] == "pending"

    assert rows[
        "push"
    ][
        "status"
    ] == "suppressed"


def test_delivery_provisioning_is_idempotent(
    app,
):
    with app.app_context():
        user = create_user()

        notification = _insert_notification(
            user_id=user[
                "_id"
            ],
        )

        first = (
            provision_notification_deliveries(
                notification
            )
        )

        second = (
            provision_notification_deliveries(
                notification
            )
        )

        count = (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": (
                        notification[
                            "_id"
                        ]
                    ),
                }
            )
        )

    assert count == 2

    assert first[
        "channels"
    ][
        "push"
    ][
        "created"
    ] is True

    assert second[
        "channels"
    ][
        "push"
    ][
        "created"
    ] is False


def test_reconciliation_repairs_missing_rows_across_pages(
    app,
):
    ids = [
        ObjectId(
            "507f1f77bcf86cd799439071"
        ),
        ObjectId(
            "507f1f77bcf86cd799439072"
        ),
        ObjectId(
            "507f1f77bcf86cd799439073"
        ),
    ]

    with app.app_context():
        user = create_user()

        for index, notification_id in enumerate(
            ids
        ):
            _insert_notification(
                user_id=user[
                    "_id"
                ],
                notification_id=notification_id,
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=index
                    )
                ),
            )

        first = (
            reconcile_notification_deliveries(
                limit=2
            )
        )

        assert first[
            "scanned"
        ] == 2

        assert first[
            "has_more"
        ] is True

        cursor = first[
            "next_cursor"
        ]

        second = (
            reconcile_notification_deliveries(
                limit=2,
                after_created_at=(
                    cursor[
                        "created_at"
                    ]
                ),
                after_notification_id=(
                    cursor[
                        "notification_id"
                    ]
                ),
            )
        )

        count = (
            mongo.db.notification_deliveries.count_documents(
                {}
            )
        )

    assert second[
        "scanned"
    ] == 1

    assert second[
        "has_more"
    ] is False

    assert count == 6


def test_create_notification_repairs_missing_delivery_on_dedupe_retry(
    app,
):
    dedupe_key = (
        "security:delivery-provisioning"
    )

    with app.app_context():
        user = create_user()

        first = (
            create_notification(
                user_id=user[
                    "_id"
                ],
                notification_type="security_alert",
                dedupe_key=dedupe_key,
                now=BASE_TIME,
            )
        )

        notification_id = ObjectId(
            first[
                "notification"
            ][
                "id"
            ]
        )

        assert (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": notification_id,
                }
            )
            == 2
        )

        mongo.db.notification_deliveries.delete_one(
            {
                "notification_id": notification_id,
                "channel": "push",
            }
        )

        retry = (
            create_notification(
                user_id=user[
                    "_id"
                ],
                notification_type="security_alert",
                dedupe_key=dedupe_key,
                now=BASE_TIME,
            )
        )

        count = (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": notification_id,
                }
            )
        )

    assert first[
        "created"
    ] is True

    assert retry[
        "created"
    ] is False

    assert count == 2


def test_provisioning_failure_does_not_break_durable_notification(
    app,
    monkeypatch,
):
    def fail_provisioning(
        *args,
        **kwargs,
    ):
        raise RuntimeError(
            "simulated provisioning failure"
        )

    monkeypatch.setattr(
        notification_service_module,
        "provision_notification_deliveries",
        fail_provisioning,
    )

    with app.app_context():
        user = create_user()

        result = (
            create_notification(
                user_id=user[
                    "_id"
                ],
                notification_type="security_alert",
                dedupe_key=(
                    "security:provisioning-failure"
                ),
                now=BASE_TIME,
            )
        )

        notification_count = (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user[
                        "_id"
                    ],
                }
            )
        )

        delivery_count = (
            mongo.db.notification_deliveries.count_documents(
                {}
            )
        )

    assert result[
        "created"
    ] is True

    assert notification_count == 1

    assert delivery_count == 0
