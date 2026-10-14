"""Integration tests for Xuoroni Notification service privacy contracts."""

import base64
import json
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
from app.services.notification_service import (
    NOTIFICATION_CURSOR_MAX_LENGTH,
    NotificationConflictError,
    NotificationLifecycleError,
    NotificationNotFoundError,
    NotificationValidationError,
    create_notification,
    decode_notification_cursor,
    encode_notification_cursor,
    get_my_unread_notification_count,
    list_my_notifications,
    mark_all_my_notifications_read,
    mark_my_notification_read,
)


TEST_DATABASE = (
    "xuoroni_test_notification_service"
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
def clean_state(
    app,
):
    with app.app_context():
        for collection in (
            mongo.db.notifications,
            mongo.db.blocks,
            mongo.db.conversations,
            mongo.db.matches,
            mongo.db.profiles,
            mongo.db.users,
        ):
            collection.delete_many(
                {}
            )

    yield

    with app.app_context():
        for collection in (
            mongo.db.notifications,
            mongo.db.blocks,
            mongo.db.conversations,
            mongo.db.matches,
            mongo.db.profiles,
            mongo.db.users,
        ):
            collection.delete_many(
                {}
            )


def _create_user(
    *,
    account_status="active",
    profile_status="active",
    onboarding_status="completed",
):
    user_id = ObjectId()

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": account_status,
        }
    )

    mongo.db.profiles.insert_one(
        {
            "user_id": user_id,
            "profile_status": profile_status,
            "onboarding_status": onboarding_status,
        }
    )

    return user_id


def _create_pair():
    user_id = _create_user()
    peer_id = _create_user()

    match_id = ObjectId()

    mongo.db.matches.insert_one(
        {
            "_id": match_id,
            "user_ids": [
                user_id,
                peer_id,
            ],
            "status": "active",
        }
    )

    conversation_id = ObjectId()

    mongo.db.conversations.insert_one(
        {
            "_id": conversation_id,
            "match_id": match_id,
            "member_ids": [
                user_id,
                peer_id,
            ],
            "status": "active",
        }
    )

    return {
        "user_id": user_id,
        "peer_id": peer_id,
        "match_id": match_id,
        "conversation_id": conversation_id,
    }


def _block(
    blocker_id,
    blocked_id,
):
    mongo.db.blocks.insert_one(
        {
            "blocker_id": blocker_id,
            "blocked_id": blocked_id,
        }
    )


def _create_security(
    user_id,
    *,
    minute=0,
    dedupe_key=None,
):
    return create_notification(
        user_id=user_id,
        notification_type="security_alert",
        dedupe_key=dedupe_key,
        now=(
            BASE_TIME
            + timedelta(
                minutes=minute
            )
        ),
    )


def test_notification_cursor_round_trip():
    notification_id = ObjectId()

    cursor = (
        encode_notification_cursor(
            created_at=BASE_TIME,
            notification_id=(
                notification_id
            ),
        )
    )

    assert isinstance(
        cursor,
        str,
    )

    assert len(
        cursor
    ) <= NOTIFICATION_CURSOR_MAX_LENGTH

    decoded = (
        decode_notification_cursor(
            cursor
        )
    )

    assert decoded == {
        "created_at": BASE_TIME,
        "notification_id": (
            notification_id
        ),
    }


def test_notification_cursor_normalizes_timezone():
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

    notification_id = ObjectId()

    cursor = (
        encode_notification_cursor(
            created_at=india_time,
            notification_id=(
                notification_id
            ),
        )
    )

    decoded = (
        decode_notification_cursor(
            cursor
        )
    )

    assert (
        decoded[
            "created_at"
        ]
        == BASE_TIME
    )


def test_empty_notification_cursor_is_none():
    assert (
        decode_notification_cursor(
            None
        )
        is None
    )

    assert (
        decode_notification_cursor(
            "   "
        )
        is None
    )


@pytest.mark.parametrize(
    "cursor",
    [
        123,
        "x" * (
            NOTIFICATION_CURSOR_MAX_LENGTH
            + 1
        ),
        "not-valid-json",
    ],
)
def test_invalid_notification_cursor_is_rejected(
    cursor,
):
    with pytest.raises(
        NotificationValidationError
    ):
        decode_notification_cursor(
            cursor
        )


def test_wrong_shape_cursor_is_rejected():
    raw = json.dumps(
        {
            "created_at": (
                BASE_TIME.isoformat()
            ),
        }
    ).encode(
        "utf-8"
    )

    cursor = (
        base64.urlsafe_b64encode(
            raw
        )
        .decode(
            "ascii"
        )
        .rstrip(
            "="
        )
    )

    with pytest.raises(
        NotificationValidationError
    ):
        decode_notification_cursor(
            cursor
        )


def test_invalid_cursor_notification_id_is_rejected():
    raw = json.dumps(
        {
            "created_at": (
                BASE_TIME.isoformat()
            ),
            "notification_id": (
                "not-an-object-id"
            ),
        }
    ).encode(
        "utf-8"
    )

    cursor = (
        base64.urlsafe_b64encode(
            raw
        )
        .decode(
            "ascii"
        )
        .rstrip(
            "="
        )
    )

    with pytest.raises(
        NotificationValidationError
    ):
        decode_notification_cursor(
            cursor
        )


def test_generic_notification_is_privacy_safe(
    app,
):
    with app.app_context():
        user_id = _create_user()

        result = _create_security(
            user_id
        )

        assert (
            result["created"]
            is True
        )

        payload = result[
            "notification"
        ]

        assert payload[
            "type"
        ] == "security_alert"

        assert payload[
            "title"
        ] == "Account security"

        assert (
            "user_id"
            not in payload
        )

        assert (
            "dedupe_key"
            not in payload
        )

        assert (
            "updated_at"
            not in payload
        )


def test_generic_notification_dedupe_is_idempotent(
    app,
):
    with app.app_context():
        user_id = _create_user()

        first = _create_security(
            user_id,
            dedupe_key=(
                "security:test-event"
            ),
        )

        retry = _create_security(
            user_id,
            minute=10,
            dedupe_key=(
                "security:test-event"
            ),
        )

        assert (
            first["created"]
            is True
        )

        assert (
            retry["created"]
            is False
        )

        assert (
            retry[
                "notification"
            ][
                "id"
            ]
            == first[
                "notification"
            ][
                "id"
            ]
        )


def test_dedupe_conflict_maps_to_service_conflict(
    app,
):
    with app.app_context():
        pair = _create_pair()

        user_id = pair[
            "user_id"
        ]

        _create_security(
            user_id,
            dedupe_key=(
                "shared:key"
            ),
        )

        with pytest.raises(
            NotificationConflictError
        ) as exc_info:
            create_notification(
                user_id=user_id,
                notification_type="new_match",
                resource_type="match",
                resource_id=pair[
                    "match_id"
                ],
                dedupe_key="shared:key",
                now=BASE_TIME,
            )

        assert (
            exc_info.value.code
            == "NOTIFICATION_DEDUPE_CONFLICT"
        )


def test_invalid_notification_type_maps_to_validation_error(
    app,
):
    with app.app_context():
        user_id = _create_user()

        with pytest.raises(
            NotificationValidationError
        ):
            create_notification(
                user_id=user_id,
                notification_type=(
                    "private_culture_update"
                ),
                now=BASE_TIME,
            )


def test_match_notification_requires_active_pair(
    app,
):
    with app.app_context():
        pair = _create_pair()

        result = create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_match",
            resource_type="match",
            resource_id=pair[
                "match_id"
            ],
            now=BASE_TIME,
        )

        assert result[
            "notification"
        ][
            "resource_id"
        ] == str(
            pair[
                "match_id"
            ]
        )


def test_conversation_notification_requires_active_match(
    app,
):
    with app.app_context():
        pair = _create_pair()

        result = create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_message",
            resource_type=(
                "conversation"
            ),
            resource_id=pair[
                "conversation_id"
            ],
            now=BASE_TIME,
        )

        assert result[
            "notification"
        ][
            "resource_id"
        ] == str(
            pair[
                "conversation_id"
            ]
        )

        mongo.db.matches.update_one(
            {
                "_id": pair[
                    "match_id"
                ],
            },
            {
                "$set": {
                    "status": "unmatched",
                }
            },
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=pair[
                    "user_id"
                ],
                notification_type=(
                    "new_message"
                ),
                resource_type=(
                    "conversation"
                ),
                resource_id=pair[
                    "conversation_id"
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )


def test_block_prevents_pair_notification_creation(
    app,
):
    with app.app_context():
        pair = _create_pair()

        _block(
            pair[
                "user_id"
            ],
            pair[
                "peer_id"
            ],
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=pair[
                    "user_id"
                ],
                notification_type=(
                    "new_match"
                ),
                resource_type="match",
                resource_id=pair[
                    "match_id"
                ],
                now=BASE_TIME,
            )


def test_peer_block_prevents_pair_notification_creation(
    app,
):
    with app.app_context():
        pair = _create_pair()

        _block(
            pair[
                "peer_id"
            ],
            pair[
                "user_id"
            ],
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=pair[
                    "user_id"
                ],
                notification_type=(
                    "new_match"
                ),
                resource_type="match",
                resource_id=pair[
                    "match_id"
                ],
                now=BASE_TIME,
            )


def test_unavailable_peer_prevents_pair_notification_creation(
    app,
):
    with app.app_context():
        pair = _create_pair()

        mongo.db.profiles.update_one(
            {
                "user_id": pair[
                    "peer_id"
                ],
            },
            {
                "$set": {
                    "profile_status": (
                        "inactive"
                    ),
                }
            },
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=pair[
                    "user_id"
                ],
                notification_type=(
                    "new_match"
                ),
                resource_type="match",
                resource_id=pair[
                    "match_id"
                ],
                now=BASE_TIME,
            )


def test_future_resource_domain_fails_closed(
    app,
):
    with app.app_context():
        user_id = _create_user()

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=user_id,
                notification_type=(
                    "verification_update"
                ),
                resource_type=(
                    "verification"
                ),
                resource_id=ObjectId(),
                now=BASE_TIME,
            )


def test_account_resource_must_belong_to_recipient(
    app,
):
    with app.app_context():
        user_id = _create_user()
        other_id = _create_user()

        allowed = create_notification(
            user_id=user_id,
            notification_type=(
                "security_alert"
            ),
            resource_type="account",
            resource_id=user_id,
            now=BASE_TIME,
        )

        assert allowed[
            "notification"
        ][
            "resource_id"
        ] == str(
            user_id
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=user_id,
                notification_type=(
                    "security_alert"
                ),
                resource_type="account",
                resource_id=other_id,
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )


def test_listing_is_recipient_scoped(
    app,
):
    with app.app_context():
        user_id = _create_user()
        other_id = _create_user()

        _create_security(
            user_id,
            minute=1,
        )

        _create_security(
            user_id,
            minute=2,
        )

        _create_security(
            other_id,
            minute=3,
        )

        page = (
            list_my_notifications(
                user_id,
                limit=10,
            )
        )

        assert (
            page["returned"]
            == 2
        )

        assert all(
            item[
                "type"
            ]
            == "security_alert"
            for item in page[
                "notifications"
            ]
        )


def test_service_cursor_pagination_has_no_overlap(
    app,
):
    with app.app_context():
        user_id = _create_user()

        for minute in range(
            5
        ):
            _create_security(
                user_id,
                minute=minute,
            )

        first = (
            list_my_notifications(
                user_id,
                limit=2,
            )
        )

        second = (
            list_my_notifications(
                user_id,
                limit=2,
                cursor=first[
                    "next_cursor"
                ],
            )
        )

        third = (
            list_my_notifications(
                user_id,
                limit=2,
                cursor=second[
                    "next_cursor"
                ],
            )
        )

        ids = [
            item["id"]
            for page in (
                first,
                second,
                third,
            )
            for item in page[
                "notifications"
            ]
        ]

        assert len(
            ids
        ) == 5

        assert len(
            set(
                ids
            )
        ) == 5

        assert first[
            "has_more"
        ] is True

        assert second[
            "has_more"
        ] is True

        assert third[
            "has_more"
        ] is False

        assert (
            third[
                "next_cursor"
            ]
            is None
        )


def test_invalid_list_query_maps_to_validation_error(
    app,
):
    with app.app_context():
        user_id = _create_user()

        with pytest.raises(
            NotificationValidationError
        ):
            list_my_notifications(
                user_id,
                limit=0,
            )

        with pytest.raises(
            NotificationValidationError
        ):
            list_my_notifications(
                user_id,
                unread_only=1,
            )


def test_block_suppresses_pair_notifications(
    app,
):
    with app.app_context():
        pair = _create_pair()

        match_notice = create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_match",
            resource_type="match",
            resource_id=pair[
                "match_id"
            ],
            now=BASE_TIME,
        )

        message_notice = create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_message",
            resource_type=(
                "conversation"
            ),
            resource_id=pair[
                "conversation_id"
            ],
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        security_notice = (
            _create_security(
                pair[
                    "user_id"
                ],
                minute=2,
            )
        )

        assert match_notice[
            "created"
        ] is True

        assert message_notice[
            "created"
        ] is True

        assert security_notice[
            "created"
        ] is True

        _block(
            pair[
                "user_id"
            ],
            pair[
                "peer_id"
            ],
        )

        page = (
            list_my_notifications(
                pair[
                    "user_id"
                ],
                limit=10,
            )
        )

        assert (
            page["returned"]
            == 1
        )

        assert page[
            "notifications"
        ][0][
            "type"
        ] == "security_alert"

        unread = (
            get_my_unread_notification_count(
                pair[
                    "user_id"
                ]
            )
        )

        assert (
            unread[
                "unread_count"
            ]
            == 1
        )


def test_unmatch_suppresses_pair_notifications(
    app,
):
    with app.app_context():
        pair = _create_pair()

        create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_match",
            resource_type="match",
            resource_id=pair[
                "match_id"
            ],
            now=BASE_TIME,
        )

        create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_message",
            resource_type=(
                "conversation"
            ),
            resource_id=pair[
                "conversation_id"
            ],
            now=(
                BASE_TIME
                + timedelta(
                    minutes=1
                )
            ),
        )

        _create_security(
            pair[
                "user_id"
            ],
            minute=2,
        )

        mongo.db.matches.update_one(
            {
                "_id": pair[
                    "match_id"
                ],
            },
            {
                "$set": {
                    "status": "unmatched",
                }
            },
        )

        page = (
            list_my_notifications(
                pair[
                    "user_id"
                ],
                limit=10,
            )
        )

        assert [
            item[
                "type"
            ]
            for item in page[
                "notifications"
            ]
        ] == [
            "security_alert",
        ]


def test_malformed_durable_notification_fails_closed(
    app,
):
    with app.app_context():
        user_id = _create_user()

        _create_security(
            user_id,
            minute=1,
        )

        mongo.db.notifications.insert_one(
            {
                "schema_version": 999,
                "user_id": user_id,
                "type": (
                    "security_alert"
                ),
                "title": (
                    "Private title"
                ),
                "body": (
                    "Private body"
                ),
                "resource_type": None,
                "resource_id": None,
                "dedupe_key": None,
                "created_at": (
                    BASE_TIME
                    + timedelta(
                        minutes=2
                    )
                ),
                "updated_at": (
                    BASE_TIME
                    + timedelta(
                        minutes=2
                    )
                ),
                "read_at": None,
                "dismissed_at": None,
            }
        )

        page = (
            list_my_notifications(
                user_id,
                limit=10,
            )
        )

        assert (
            page["returned"]
            == 1
        )

        assert page[
            "notifications"
        ][0][
            "type"
        ] == "security_alert"


def test_unread_count_includes_only_visible_notifications(
    app,
):
    with app.app_context():
        pair = _create_pair()

        create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_match",
            resource_type="match",
            resource_id=pair[
                "match_id"
            ],
            now=BASE_TIME,
        )

        _create_security(
            pair[
                "user_id"
            ],
            minute=1,
        )

        assert (
            get_my_unread_notification_count(
                pair[
                    "user_id"
                ]
            )[
                "unread_count"
            ]
            == 2
        )

        _block(
            pair[
                "peer_id"
            ],
            pair[
                "user_id"
            ],
        )

        assert (
            get_my_unread_notification_count(
                pair[
                    "user_id"
                ]
            )[
                "unread_count"
            ]
            == 1
        )


def test_mark_notification_read_is_recipient_scoped(
    app,
):
    with app.app_context():
        user_id = _create_user()
        other_id = _create_user()

        created = (
            _create_security(
                user_id
            )
        )

        notification_id = (
            created[
                "notification"
            ][
                "id"
            ]
        )

        with pytest.raises(
            NotificationNotFoundError
        ):
            mark_my_notification_read(
                user_id=other_id,
                notification_id=(
                    notification_id
                ),
                now=BASE_TIME,
            )

        result = (
            mark_my_notification_read(
                user_id=user_id,
                notification_id=(
                    notification_id
                ),
                now=BASE_TIME,
            )
        )

        assert (
            result["changed"]
            is True
        )

        retry = (
            mark_my_notification_read(
                user_id=user_id,
                notification_id=(
                    notification_id
                ),
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        assert (
            retry["changed"]
            is False
        )


def test_hidden_pair_notification_cannot_be_marked_read(
    app,
):
    with app.app_context():
        pair = _create_pair()

        created = create_notification(
            user_id=pair[
                "user_id"
            ],
            notification_type="new_match",
            resource_type="match",
            resource_id=pair[
                "match_id"
            ],
            now=BASE_TIME,
        )

        notification_id = (
            created[
                "notification"
            ][
                "id"
            ]
        )

        _block(
            pair[
                "user_id"
            ],
            pair[
                "peer_id"
            ],
        )

        with pytest.raises(
            NotificationNotFoundError
        ):
            mark_my_notification_read(
                user_id=pair[
                    "user_id"
                ],
                notification_id=(
                    notification_id
                ),
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )

        stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": ObjectId(
                        notification_id
                    ),
                }
            )
        )

        assert (
            stored[
                "read_at"
            ]
            is None
        )


def test_mark_all_read_only_changes_visible_notifications(
    app,
):
    with app.app_context():
        pair = _create_pair()

        pair_notice = (
            create_notification(
                user_id=pair[
                    "user_id"
                ],
                notification_type=(
                    "new_match"
                ),
                resource_type="match",
                resource_id=pair[
                    "match_id"
                ],
                now=BASE_TIME,
            )
        )

        security_notice = (
            _create_security(
                pair[
                    "user_id"
                ],
                minute=1,
            )
        )

        _block(
            pair[
                "user_id"
            ],
            pair[
                "peer_id"
            ],
        )

        changed = (
            mark_all_my_notifications_read(
                pair[
                    "user_id"
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=2
                    )
                ),
            )
        )

        assert (
            changed[
                "changed"
            ]
            == 1
        )

        pair_stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": ObjectId(
                        pair_notice[
                            "notification"
                        ][
                            "id"
                        ]
                    ),
                }
            )
        )

        security_stored = (
            mongo.db.notifications.find_one(
                {
                    "_id": ObjectId(
                        security_notice[
                            "notification"
                        ][
                            "id"
                        ]
                    ),
                }
            )
        )

        assert (
            pair_stored[
                "read_at"
            ]
            is None
        )

        assert (
            security_stored[
                "read_at"
            ]
            is not None
        )


def test_suspended_current_account_is_rejected(
    app,
):
    with app.app_context():
        user_id = _create_user(
            account_status="suspended"
        )

        with pytest.raises(
            NotificationLifecycleError
        ):
            list_my_notifications(
                user_id
            )

        with pytest.raises(
            NotificationLifecycleError
        ):
            get_my_unread_notification_count(
                user_id
            )

        with pytest.raises(
            NotificationLifecycleError
        ):
            create_notification(
                user_id=user_id,
                notification_type=(
                    "security_alert"
                ),
                now=BASE_TIME,
            )


def test_missing_current_user_is_rejected(
    app,
):
    with app.app_context():
        with pytest.raises(
            NotificationNotFoundError
        ):
            list_my_notifications(
                ObjectId()
            )


def test_invalid_current_user_id_is_validation_error(
    app,
):
    with app.app_context():
        with pytest.raises(
            NotificationValidationError
        ):
            list_my_notifications(
                "invalid-user"
            )


def test_unread_filter_returns_only_unread_visible_notifications(
    app,
):
    with app.app_context():
        user_id = _create_user()

        first = _create_security(
            user_id,
            minute=1,
        )

        second = _create_security(
            user_id,
            minute=2,
        )

        mark_my_notification_read(
            user_id=user_id,
            notification_id=(
                second[
                    "notification"
                ][
                    "id"
                ]
            ),
            now=(
                BASE_TIME
                + timedelta(
                    minutes=3
                )
            ),
        )

        page = (
            list_my_notifications(
                user_id,
                unread_only=True,
                limit=10,
            )
        )

        assert (
            page["returned"]
            == 1
        )

        assert page[
            "notifications"
        ][0][
            "id"
        ] == first[
            "notification"
        ][
            "id"
        ]


def test_dismissed_notification_is_not_visible_or_counted(
    app,
):
    with app.app_context():
        user_id = _create_user()

        result = _create_security(
            user_id
        )

        notification_id = (
            ObjectId(
                result[
                    "notification"
                ][
                    "id"
                ]
            )
        )

        mongo.db.notifications.update_one(
            {
                "_id": (
                    notification_id
                ),
            },
            {
                "$set": {
                    "dismissed_at": (
                        BASE_TIME
                        + timedelta(
                            minutes=1
                        )
                    ),
                }
            },
        )

        page = (
            list_my_notifications(
                user_id,
                limit=10,
            )
        )

        assert (
            page["returned"]
            == 0
        )

        assert (
            get_my_unread_notification_count(
                user_id
            )[
                "unread_count"
            ]
            == 0
        )
