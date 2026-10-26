"""MongoDB integration tests for Soultie Notification persistence."""

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
import app.repositories.notification_repository as notification_repository
from app.repositories.notification_repository import (
    DEFAULT_NOTIFICATION_PAGE_SIZE,
    MAX_NOTIFICATION_PAGE_SIZE,
    count_unread_notifications,
    create_notification_idempotent,
    get_notification_by_id_for_user,
    list_notifications_for_user,
    mark_all_notifications_read,
    mark_notification_read,
)


TEST_DATABASE = (
    "xuoroni_test_notification_repository"
)

BASE_TIME = datetime(
    2026,
    10,
    14,
    12,
    0,
    tzinfo=timezone.utc,
)


def _as_utc(
    value,
):
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
def clean_notifications(
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


def _create(
    *,
    user_id,
    notification_type="security_alert",
    resource_type=None,
    resource_id=None,
    dedupe_key=None,
    now=BASE_TIME,
):
    return create_notification_idempotent(
        user_id=user_id,
        notification_type=notification_type,
        resource_type=resource_type,
        resource_id=resource_id,
        dedupe_key=dedupe_key,
        now=now,
    )


def test_notification_repository_page_constants():
    assert (
        DEFAULT_NOTIFICATION_PAGE_SIZE
        == 20
    )

    assert (
        MAX_NOTIFICATION_PAGE_SIZE
        == 50
    )


def test_notification_creation_without_dedupe_is_not_collapsed(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        first = _create(
            user_id=user_id,
        )

        second = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        assert first["created"] is True
        assert second["created"] is True

        assert (
            first["notification"]["_id"]
            != second["notification"]["_id"]
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 2
        )


def test_notification_creation_with_dedupe_is_idempotent(
    app,
):
    user_id = ObjectId()
    match_id = ObjectId()

    dedupe_key = (
        "match.created:"
        + str(match_id)
    )

    with app.app_context():
        first = _create(
            user_id=user_id,
            notification_type="new_match",
            resource_type="match",
            resource_id=match_id,
            dedupe_key=dedupe_key,
            now=BASE_TIME,
        )

        retry = _create(
            user_id=user_id,
            notification_type="new_match",
            resource_type="match",
            resource_id=match_id,
            dedupe_key=dedupe_key,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=10
                )
            ),
        )

        assert first["created"] is True
        assert retry["created"] is False

        assert (
            retry["notification"]["_id"]
            == first["notification"]["_id"]
        )

        assert (
            _as_utc(
                retry[
                    "notification"
                ][
                    "created_at"
                ]
            )
            == BASE_TIME
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


def test_dedupe_identity_conflict_is_rejected(
    app,
):
    user_id = ObjectId()
    match_id = ObjectId()

    dedupe_key = (
        "match.created:"
        + str(match_id)
    )

    with app.app_context():
        _create(
            user_id=user_id,
            notification_type="new_match",
            resource_type="match",
            resource_id=match_id,
            dedupe_key=dedupe_key,
        )

        with pytest.raises(
            ValueError,
            match=(
                "already used for a different "
                "notification"
            ),
        ):
            _create(
                user_id=user_id,
                notification_type=(
                    "security_alert"
                ),
                dedupe_key=dedupe_key,
            )


def test_duplicate_key_race_recovers_existing_notification(
    app,
    monkeypatch,
):
    user_id = ObjectId()
    match_id = ObjectId()

    dedupe_key = (
        "match.created:"
        + str(match_id)
    )

    with app.app_context():
        first = _create(
            user_id=user_id,
            notification_type="new_match",
            resource_type="match",
            resource_id=match_id,
            dedupe_key=dedupe_key,
        )

        original_lookup = (
            notification_repository
            ._find_notification_by_dedupe_key
        )

        calls = {
            "count": 0,
        }

        def simulated_race_lookup(
            recipient_id,
            key,
        ):
            calls["count"] += 1

            if calls["count"] == 1:
                return None

            return original_lookup(
                recipient_id,
                key,
            )

        monkeypatch.setattr(
            notification_repository,
            "_find_notification_by_dedupe_key",
            simulated_race_lookup,
        )

        retry = _create(
            user_id=user_id,
            notification_type="new_match",
            resource_type="match",
            resource_id=match_id,
            dedupe_key=dedupe_key,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        assert calls["count"] == 2
        assert retry["created"] is False

        assert (
            retry["notification"]["_id"]
            == first["notification"]["_id"]
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


def test_same_dedupe_key_is_recipient_scoped(
    app,
):
    first_user = ObjectId()
    second_user = ObjectId()

    dedupe_key = (
        "system:regional-announcement"
    )

    with app.app_context():
        first = _create(
            user_id=first_user,
            dedupe_key=dedupe_key,
        )

        second = _create(
            user_id=second_user,
            dedupe_key=dedupe_key,
        )

        assert first["created"] is True
        assert second["created"] is True

        assert (
            first["notification"]["_id"]
            != second["notification"]["_id"]
        )


def test_get_notification_is_recipient_owned(
    app,
):
    owner_id = ObjectId()
    other_id = ObjectId()

    with app.app_context():
        created = _create(
            user_id=owner_id,
        )

        notification_id = (
            created[
                "notification"
            ][
                "_id"
            ]
        )

        owned = (
            get_notification_by_id_for_user(
                str(notification_id),
                str(owner_id),
            )
        )

        foreign = (
            get_notification_by_id_for_user(
                notification_id,
                other_id,
            )
        )

        invalid = (
            get_notification_by_id_for_user(
                "invalid-notification",
                owner_id,
            )
        )

        assert owned is not None
        assert owned["_id"] == notification_id
        assert foreign is None
        assert invalid is None


def test_list_notifications_is_recipient_scoped_and_ordered(
    app,
):
    user_id = ObjectId()
    other_id = ObjectId()

    with app.app_context():
        oldest = _create(
            user_id=user_id,
            now=BASE_TIME,
        )["notification"]

        middle = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )["notification"]

        newest = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=2
                )
            ),
        )["notification"]

        _create(
            user_id=other_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=3
                )
            ),
        )

        page = (
            list_notifications_for_user(
                user_id
            )
        )

        assert page["has_more"] is False

        assert [
            item["_id"]
            for item in page[
                "notifications"
            ]
        ] == [
            newest["_id"],
            middle["_id"],
            oldest["_id"],
        ]


def test_dismissed_notifications_are_hidden_by_default(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        visible = _create(
            user_id=user_id,
        )["notification"]

        dismissed = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )["notification"]

        mongo.db.notifications.update_one(
            {
                "_id": dismissed["_id"],
            },
            {
                "$set": {
                    "dismissed_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=2
                        )
                    ),
                }
            },
        )

        default_page = (
            list_notifications_for_user(
                user_id
            )
        )

        full_page = (
            list_notifications_for_user(
                user_id,
                include_dismissed=True,
            )
        )

        assert [
            item["_id"]
            for item in default_page[
                "notifications"
            ]
        ] == [
            visible["_id"],
        ]

        assert {
            item["_id"]
            for item in full_page[
                "notifications"
            ]
        } == {
            visible["_id"],
            dismissed["_id"],
        }


def test_unread_only_filter_respects_dismissal(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        unread = _create(
            user_id=user_id,
        )["notification"]

        read = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )["notification"]

        dismissed = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=2
                )
            ),
        )["notification"]

        mongo.db.notifications.update_one(
            {
                "_id": read["_id"],
            },
            {
                "$set": {
                    "read_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        mongo.db.notifications.update_one(
            {
                "_id": dismissed["_id"],
            },
            {
                "$set": {
                    "dismissed_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        default_unread = (
            list_notifications_for_user(
                user_id,
                unread_only=True,
            )
        )

        all_unread = (
            list_notifications_for_user(
                user_id,
                unread_only=True,
                include_dismissed=True,
            )
        )

        assert [
            item["_id"]
            for item in default_unread[
                "notifications"
            ]
        ] == [
            unread["_id"],
        ]

        assert {
            item["_id"]
            for item in all_unread[
                "notifications"
            ]
        } == {
            unread["_id"],
            dismissed["_id"],
        }


def test_notification_cursor_pagination_is_stable(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        created = []

        for _ in range(5):
            created.append(
                _create(
                    user_id=user_id,
                    now=BASE_TIME,
                )[
                    "notification"
                ]
            )

        first_page = (
            list_notifications_for_user(
                user_id,
                limit=2,
            )
        )

        assert first_page["has_more"] is True
        assert len(
            first_page["notifications"]
        ) == 2

        first_tail = (
            first_page[
                "notifications"
            ][-1]
        )

        second_page = (
            list_notifications_for_user(
                user_id,
                limit=2,
                cursor={
                    "created_at": (
                        first_tail[
                            "created_at"
                        ]
                    ),
                    "notification_id": (
                        first_tail["_id"]
                    ),
                },
            )
        )

        assert second_page["has_more"] is True
        assert len(
            second_page["notifications"]
        ) == 2

        second_tail = (
            second_page[
                "notifications"
            ][-1]
        )

        third_page = (
            list_notifications_for_user(
                user_id,
                limit=2,
                cursor={
                    "created_at": (
                        second_tail[
                            "created_at"
                        ]
                    ),
                    "notification_id": (
                        str(
                            second_tail[
                                "_id"
                            ]
                        )
                    ),
                },
            )
        )

        assert third_page["has_more"] is False
        assert len(
            third_page["notifications"]
        ) == 1

        seen = [
            item["_id"]
            for page in (
                first_page,
                second_page,
                third_page,
            )
            for item in page[
                "notifications"
            ]
        ]

        assert len(seen) == 5
        assert len(set(seen)) == 5

        expected = sorted(
            [
                item["_id"]
                for item in created
            ],
            reverse=True,
        )

        assert seen == expected


def test_numeric_string_page_size_is_supported(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        for minute in range(3):
            _create(
                user_id=user_id,
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=minute
                    )
                ),
            )

        page = (
            list_notifications_for_user(
                user_id,
                limit="2",
            )
        )

        assert len(
            page["notifications"]
        ) == 2

        assert page["has_more"] is True


@pytest.mark.parametrize(
    "limit",
    [
        0,
        51,
        True,
        None,
        "not-a-number",
    ],
)
def test_invalid_page_size_is_rejected(
    app,
    limit,
):
    user_id = ObjectId()

    with app.app_context():
        with pytest.raises(
            ValueError,
            match=(
                "Invalid notification page size"
            ),
        ):
            list_notifications_for_user(
                user_id,
                limit=limit,
            )


@pytest.mark.parametrize(
    "cursor",
    [
        "invalid-cursor",
        {},
        {
            "created_at": BASE_TIME,
            "notification_id": (
                "invalid-id"
            ),
        },
        {
            "created_at": (
                "invalid-time"
            ),
            "notification_id": (
                ObjectId()
            ),
        },
    ],
)
def test_invalid_cursor_is_rejected(
    app,
    cursor,
):
    user_id = ObjectId()

    with app.app_context():
        with pytest.raises(
            ValueError,
            match=(
                "Invalid notification cursor"
            ),
        ):
            list_notifications_for_user(
                user_id,
                cursor=cursor,
            )


@pytest.mark.parametrize(
    (
        "argument",
        "value",
    ),
    [
        (
            "unread_only",
            1,
        ),
        (
            "include_dismissed",
            "false",
        ),
    ],
)
def test_invalid_listing_boolean_flags_are_rejected(
    app,
    argument,
    value,
):
    user_id = ObjectId()

    kwargs = {
        argument: value,
    }

    with app.app_context():
        with pytest.raises(
            ValueError
        ):
            list_notifications_for_user(
                user_id,
                **kwargs,
            )


def test_unread_count_is_recipient_scoped_and_excludes_dismissed(
    app,
):
    user_id = ObjectId()
    other_id = ObjectId()

    with app.app_context():
        unread = _create(
            user_id=user_id,
        )["notification"]

        read = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )["notification"]

        dismissed = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=2
                )
            ),
        )["notification"]

        _create(
            user_id=other_id,
        )

        mongo.db.notifications.update_one(
            {
                "_id": read["_id"],
            },
            {
                "$set": {
                    "read_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        mongo.db.notifications.update_one(
            {
                "_id": dismissed["_id"],
            },
            {
                "$set": {
                    "dismissed_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        assert unread["_id"] is not None

        assert (
            count_unread_notifications(
                user_id
            )
            == 1
        )

        assert (
            count_unread_notifications(
                other_id
            )
            == 1
        )


def test_mark_notification_read_is_recipient_scoped_and_idempotent(
    app,
):
    owner_id = ObjectId()
    other_id = ObjectId()

    read_time = (
        BASE_TIME
        + timedelta(
            minutes=5
        )
    )

    later_time = (
        BASE_TIME
        + timedelta(
            minutes=10
        )
    )

    with app.app_context():
        notification = _create(
            user_id=owner_id,
        )["notification"]

        foreign_attempt = (
            mark_notification_read(
                notification["_id"],
                other_id,
                now=read_time,
            )
        )

        assert foreign_attempt == {
            "notification": None,
            "changed": False,
        }

        first = (
            mark_notification_read(
                notification["_id"],
                owner_id,
                now=read_time,
            )
        )

        assert first["changed"] is True

        assert (
            _as_utc(
                first[
                    "notification"
                ][
                    "read_at"
                ]
            )
            == read_time
        )

        retry = (
            mark_notification_read(
                notification["_id"],
                owner_id,
                now=later_time,
            )
        )

        assert retry["changed"] is False

        assert (
            _as_utc(
                retry[
                    "notification"
                ][
                    "read_at"
                ]
            )
            == read_time
        )


def test_mark_notification_read_normalizes_timezone(
    app,
):
    user_id = ObjectId()

    india_time = datetime(
        2026,
        10,
        14,
        17,
        30,
        tzinfo=timezone(
            timedelta(
                hours=5,
                minutes=30,
            )
        ),
    )

    with app.app_context():
        notification = _create(
            user_id=user_id,
        )["notification"]

        result = (
            mark_notification_read(
                notification["_id"],
                user_id,
                now=india_time,
            )
        )

        assert (
            _as_utc(
                result[
                    "notification"
                ][
                    "read_at"
                ]
            )
            == BASE_TIME
        )


def test_mark_all_notifications_read_is_recipient_scoped(
    app,
):
    user_id = ObjectId()
    other_id = ObjectId()

    with app.app_context():
        unread = _create(
            user_id=user_id,
        )["notification"]

        already_read = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )["notification"]

        dismissed = _create(
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    minutes=2
                )
            ),
        )["notification"]

        foreign = _create(
            user_id=other_id,
        )["notification"]

        mongo.db.notifications.update_one(
            {
                "_id": already_read["_id"],
            },
            {
                "$set": {
                    "read_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        mongo.db.notifications.update_one(
            {
                "_id": dismissed["_id"],
            },
            {
                "$set": {
                    "dismissed_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=3
                        )
                    ),
                }
            },
        )

        changed = (
            mark_all_notifications_read(
                user_id,
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=4
                    )
                ),
            )
        )

        assert changed == 1

        unread_stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": unread["_id"],
                }
            )
        )

        dismissed_stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": dismissed["_id"],
                }
            )
        )

        foreign_stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": foreign["_id"],
                }
            )
        )

        assert (
            unread_stored[
                "read_at"
            ]
            is not None
        )

        assert (
            dismissed_stored[
                "read_at"
            ]
            is None
        )

        assert (
            foreign_stored[
                "read_at"
            ]
            is None
        )


def test_invalid_recipient_operations_fail_closed(
    app,
):
    notification_id = ObjectId()

    with app.app_context():
        assert (
            get_notification_by_id_for_user(
                notification_id,
                "invalid-user",
            )
            is None
        )

        assert (
            list_notifications_for_user(
                "invalid-user"
            )
            == {
                "notifications": [],
                "has_more": False,
            }
        )

        assert (
            count_unread_notifications(
                "invalid-user"
            )
            == 0
        )

        assert (
            mark_notification_read(
                notification_id,
                "invalid-user",
            )
            == {
                "notification": None,
                "changed": False,
            }
        )

        assert (
            mark_all_notifications_read(
                "invalid-user"
            )
            == 0
        )


def test_reconciliation_scanner_empty_state(
    app,
):
    from app.repositories.notification_repository import (
        list_notifications_for_delivery_reconciliation,
    )

    with app.app_context():
        mongo.db.notifications.delete_many(
            {}
        )

        result = (
            list_notifications_for_delivery_reconciliation(
                limit=10
            )
        )

    assert result == {
        "notifications": [],
        "has_more": False,
        "next_cursor": None,
    }


def test_reconciliation_scanner_is_global_and_chronological(
    app,
):
    from datetime import (
        datetime,
        timedelta,
        timezone,
    )

    from bson import ObjectId

    from app.repositories.notification_repository import (
        list_notifications_for_delivery_reconciliation,
    )

    from app.schemas.notification import (
        build_notification_document,
    )

    base = datetime(
        2026,
        10,
        16,
        10,
        0,
        tzinfo=timezone.utc,
    )

    ids = [
        ObjectId(
            "507f1f77bcf86cd799439041"
        ),
        ObjectId(
            "507f1f77bcf86cd799439042"
        ),
        ObjectId(
            "507f1f77bcf86cd799439043"
        ),
    ]

    first_user = ObjectId()
    second_user = ObjectId()

    documents = []

    first = build_notification_document(
        user_id=first_user,
        notification_type="system_announcement",
        now=base,
    )

    first[
        "_id"
    ] = ids[
        1
    ]

    documents.append(
        first
    )

    second = build_notification_document(
        user_id=second_user,
        notification_type="system_announcement",
        now=base,
    )

    second[
        "_id"
    ] = ids[
        0
    ]

    documents.append(
        second
    )

    third = build_notification_document(
        user_id=first_user,
        notification_type="system_announcement",
        now=(
            base
            + timedelta(
                seconds=1
            )
        ),
    )

    third[
        "_id"
    ] = ids[
        2
    ]

    documents.append(
        third
    )

    with app.app_context():
        mongo.db.notifications.insert_many(
            documents
        )

        result = (
            list_notifications_for_delivery_reconciliation(
                limit=10
            )
        )

    assert [
        item[
            "_id"
        ]
        for item in result[
            "notifications"
        ]
    ] == ids

    assert {
        item[
            "user_id"
        ]
        for item in result[
            "notifications"
        ]
    } == {
        first_user,
        second_user,
    }

    assert result[
        "has_more"
    ] is False

    assert result[
        "next_cursor"
    ] is None


def test_reconciliation_scanner_cursor_pagination_is_stable(
    app,
):
    from datetime import (
        datetime,
        timedelta,
        timezone,
    )

    from bson import ObjectId

    from app.repositories.notification_repository import (
        list_notifications_for_delivery_reconciliation,
    )

    from app.schemas.notification import (
        build_notification_document,
    )

    base = datetime(
        2026,
        10,
        16,
        10,
        30,
        tzinfo=timezone.utc,
    )

    ids = [
        ObjectId(
            "507f1f77bcf86cd799439051"
        ),
        ObjectId(
            "507f1f77bcf86cd799439052"
        ),
        ObjectId(
            "507f1f77bcf86cd799439053"
        ),
    ]

    user_id = ObjectId()

    documents = []

    for index, notification_id in enumerate(
        ids
    ):
        timestamp = (
            base
            if index < 2
            else (
                base
                + timedelta(
                    seconds=1
                )
            )
        )

        document = (
            build_notification_document(
                user_id=user_id,
                notification_type="system_announcement",
                now=timestamp,
            )
        )

        document[
            "_id"
        ] = notification_id

        documents.append(
            document
        )

    with app.app_context():
        mongo.db.notifications.insert_many(
            documents
        )

        first_page = (
            list_notifications_for_delivery_reconciliation(
                limit=2
            )
        )

        cursor = first_page[
            "next_cursor"
        ]

        second_page = (
            list_notifications_for_delivery_reconciliation(
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

    assert [
        item[
            "_id"
        ]
        for item in first_page[
            "notifications"
        ]
    ] == ids[
        :2
    ]

    assert first_page[
        "has_more"
    ] is True

    assert cursor is not None

    assert cursor[
        "created_at"
    ].tzinfo is not None

    assert cursor[
        "notification_id"
    ] == ids[
        1
    ]

    assert [
        item[
            "_id"
        ]
        for item in second_page[
            "notifications"
        ]
    ] == [
        ids[
            2
        ]
    ]

    assert second_page[
        "has_more"
    ] is False

    assert second_page[
        "next_cursor"
    ] is None


def test_reconciliation_scanner_rejects_malformed_cursor(
    app,
):
    from datetime import (
        datetime,
        timezone,
    )

    from bson import ObjectId

    from app.repositories.notification_repository import (
        list_notifications_for_delivery_reconciliation,
    )

    aware = datetime(
        2026,
        10,
        16,
        11,
        0,
        tzinfo=timezone.utc,
    )

    naive = datetime(
        2026,
        10,
        16,
        11,
        0,
    )

    with app.app_context():
        with pytest.raises(
            ValueError,
            match="must include both",
        ):
            list_notifications_for_delivery_reconciliation(
                after_created_at=aware,
            )

        with pytest.raises(
            ValueError,
            match="must include both",
        ):
            list_notifications_for_delivery_reconciliation(
                after_notification_id=ObjectId(),
            )

        with pytest.raises(
            ValueError,
            match="Invalid Notification reconciliation",
        ):
            list_notifications_for_delivery_reconciliation(
                after_created_at=aware,
                after_notification_id="invalid",
            )

        normalized = (
            list_notifications_for_delivery_reconciliation(
                after_created_at=naive,
                after_notification_id=ObjectId(),
            )
        )

        assert isinstance(
            normalized,
            dict,
        )

        assert set(
            normalized
        ) == {
            "notifications",
            "has_more",
            "next_cursor",
        }
