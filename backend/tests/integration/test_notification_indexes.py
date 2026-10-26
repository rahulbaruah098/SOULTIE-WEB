"""MongoDB integration tests for Soultie Notification indexes."""

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


@pytest.fixture(autouse=True)
def clean_notification_delivery_state(
    app,
):
    with app.app_context():
        mongo.db.notification_deliveries.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notification_deliveries.delete_many(
            {}
        )


def _notification_delivery(
    *,
    notification_id=None,
    user_id=None,
    channel="realtime",
):
    from app.schemas.notification_delivery import (
        build_notification_delivery_document,
    )

    return build_notification_delivery_document(
        notification_id=(
            notification_id
            or ObjectId()
        ),
        user_id=(
            user_id
            or ObjectId()
        ),
        channel=channel,
        now=BASE_TIME,
    )


def test_notification_delivery_indexes_are_created(
    app,
):
    with app.app_context():
        information = (
            mongo.db.notification_deliveries
            .index_information()
        )

    unique_name = (
        "notification_delivery_notification_channel_unique"
    )

    claim_name = (
        "notification_delivery_pending_claim"
    )

    lease_name = (
        "notification_delivery_lease_recovery"
    )

    assert (
        unique_name
        in information
    )

    assert (
        claim_name
        in information
    )

    assert (
        lease_name
        in information
    )

    assert (
        information[
            unique_name
        ][
            "key"
        ]
        == [
            (
                "notification_id",
                1,
            ),
            (
                "channel",
                1,
            ),
        ]
    )

    assert (
        information[
            unique_name
        ].get(
            "unique"
        )
        is True
    )

    assert (
        information[
            claim_name
        ][
            "key"
        ]
        == [
            (
                "status",
                1,
            ),
            (
                "next_attempt_at",
                1,
            ),
            (
                "created_at",
                1,
            ),
        ]
    )

    assert (
        information[
            lease_name
        ][
            "key"
        ]
        == [
            (
                "status",
                1,
            ),
            (
                "lease_expires_at",
                1,
            ),
        ]
    )

    for name in (
        unique_name,
        claim_name,
        lease_name,
    ):
        assert (
            "expireAfterSeconds"
            not in information[
                name
            ]
        )

        assert (
            "partialFilterExpression"
            not in information[
                name
            ]
        )

        assert (
            "sparse"
            not in information[
                name
            ]
        )


def test_same_notification_and_delivery_channel_is_unique(
    app,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=notification_id,
                user_id=user_id,
                channel="realtime",
            )
        )

        with pytest.raises(
            DuplicateKeyError
        ):
            mongo.db.notification_deliveries.insert_one(
                _notification_delivery(
                    notification_id=notification_id,
                    user_id=user_id,
                    channel="realtime",
                )
            )

        assert (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": notification_id,
                    "channel": "realtime",
                }
            )
            == 1
        )


def test_same_notification_supports_independent_delivery_channels(
    app,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=notification_id,
                user_id=user_id,
                channel="realtime",
            )
        )

        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=notification_id,
                user_id=user_id,
                channel="email",
            )
        )

        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=notification_id,
                user_id=user_id,
                channel="push",
            )
        )

        deliveries = list(
            mongo.db.notification_deliveries.find(
                {
                    "notification_id": notification_id,
                }
            )
        )

    assert (
        len(
            deliveries
        )
        == 3
    )

    assert {
        delivery[
            "channel"
        ]
        for delivery in deliveries
    } == {
        "realtime",
        "email",
        "push",
    }


def test_same_delivery_channel_supports_independent_notifications(
    app,
):
    user_id = ObjectId()

    first_notification_id = ObjectId()
    second_notification_id = ObjectId()

    with app.app_context():
        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=first_notification_id,
                user_id=user_id,
                channel="realtime",
            )
        )

        mongo.db.notification_deliveries.insert_one(
            _notification_delivery(
                notification_id=second_notification_id,
                user_id=user_id,
                channel="realtime",
            )
        )

        assert (
            mongo.db.notification_deliveries.count_documents(
                {
                    "user_id": user_id,
                    "channel": "realtime",
                }
            )
            == 2
        )


def test_notification_delivery_index_initialization_is_idempotent(
    app,
):
    with app.app_context():
        before = (
            mongo.db.notification_deliveries
            .index_information()
        )

        ensure_indexes(
            mongo.db
        )

        after = (
            mongo.db.notification_deliveries
            .index_information()
        )

    names = (
        "notification_delivery_notification_channel_unique",
        "notification_delivery_pending_claim",
        "notification_delivery_lease_recovery",
    )

    for name in names:
        assert (
            name
            in before
        )

        assert (
            name
            in after
        )

        assert (
            after[
                name
            ][
                "key"
            ]
            == before[
                name
            ][
                "key"
            ]
        )

    assert (
        after[
            "notification_delivery_notification_channel_unique"
        ].get(
            "unique"
        )
        is True
    )


def test_notification_reconciliation_index_is_created(
    app,
):
    index_name = (
        "notification_delivery_reconciliation_scan"
    )

    with app.app_context():
        information = (
            mongo.db.notifications.index_information()
        )

    assert (
        index_name
        in information
    )

    assert (
        information[
            index_name
        ][
            "key"
        ]
        == [
            (
                "created_at",
                1,
            ),
            (
                "_id",
                1,
            ),
        ]
    )

    assert (
        information[
            index_name
        ].get(
            "unique",
            False,
        )
        is False
    )

    assert (
        "expireAfterSeconds"
        not in information[
            index_name
        ]
    )

    assert (
        "partialFilterExpression"
        not in information[
            index_name
        ]
    )

    assert (
        "sparse"
        not in information[
            index_name
        ]
    )


def test_notification_reconciliation_index_supports_deterministic_scan(
    app,
):
    from datetime import (
        datetime,
        timedelta,
        timezone,
    )

    index_name = (
        "notification_delivery_reconciliation_scan"
    )

    base = datetime(
        2026,
        10,
        16,
        9,
        0,
        tzinfo=timezone.utc,
    )

    ids = [
        ObjectId(
            "507f1f77bcf86cd799439031"
        ),
        ObjectId(
            "507f1f77bcf86cd799439032"
        ),
        ObjectId(
            "507f1f77bcf86cd799439033"
        ),
    ]

    with app.app_context():
        mongo.db.notifications.delete_many(
            {
                "_id": {
                    "$in": ids,
                }
            }
        )

        try:
            mongo.db.notifications.insert_many(
                [
                    {
                        "_id": ids[
                            1
                        ],
                        "created_at": base,
                    },
                    {
                        "_id": ids[
                            0
                        ],
                        "created_at": base,
                    },
                    {
                        "_id": ids[
                            2
                        ],
                        "created_at": (
                            base
                            + timedelta(
                                seconds=1
                            )
                        ),
                    },
                ]
            )

            rows = list(
                mongo.db.notifications.find(
                    {
                        "_id": {
                            "$in": ids,
                        }
                    }
                )
                .sort(
                    [
                        (
                            "created_at",
                            1,
                        ),
                        (
                            "_id",
                            1,
                        ),
                    ]
                )
                .hint(
                    index_name
                )
            )

            assert [
                row[
                    "_id"
                ]
                for row in rows
            ] == ids

            before = (
                mongo.db.notifications.index_information()
            )

            ensure_indexes(
                mongo.db
            )

            after = (
                mongo.db.notifications.index_information()
            )

            assert (
                after[
                    index_name
                ][
                    "key"
                ]
                == before[
                    index_name
                ][
                    "key"
                ]
            )

        finally:
            mongo.db.notifications.delete_many(
                {
                    "_id": {
                        "$in": ids,
                    }
                }
            )
