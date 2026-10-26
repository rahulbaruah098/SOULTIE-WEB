"""MongoDB integration tests for Soultie Notification Preferences persistence."""

from concurrent.futures import (
    ThreadPoolExecutor,
)
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from threading import Barrier

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.notification_preferences_repository import (
    ensure_notification_preferences_for_user,
    get_notification_preferences_for_user,
    update_notification_preferences_for_user,
)
from app.schemas.notification_preferences import (
    DISABLEABLE_IN_APP_NOTIFICATION_TYPES,
    build_notification_preferences_document,
)


TEST_DATABASE = (
    "xuoroni_test_notification_preferences_repository"
)


BASE_TIME = datetime(
    2026,
    10,
    15,
    14,
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
def clean_notification_preferences(
    app,
):
    with app.app_context():
        mongo.db.notification_preferences.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notification_preferences.delete_many(
            {}
        )


def _disableable_types():
    values = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    assert len(values) >= 2

    return values


def test_notification_preferences_unique_user_index(
    app,
):
    with app.app_context():
        information = (
            mongo.db.notification_preferences
            .index_information()
        )

    assert (
        "notification_preferences_user_unique"
        in information
    )

    definition = information[
        "notification_preferences_user_unique"
    ]

    assert (
        definition.get(
            "unique"
        )
        is True
    )

    assert (
        definition[
            "key"
        ]
        == [
            (
                "user_id",
                1,
            )
        ]
    )


def test_notification_preferences_unique_index_rejects_duplicate_user(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        mongo.db.notification_preferences.insert_one(
            build_notification_preferences_document(
                user_id=user_id,
                now=BASE_TIME,
            )
        )

        with pytest.raises(
            DuplicateKeyError
        ):
            mongo.db.notification_preferences.insert_one(
                build_notification_preferences_document(
                    user_id=user_id,
                    now=(
                        BASE_TIME
                        + timedelta(
                            minutes=1
                        )
                    ),
                )
            )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )


def test_get_notification_preferences_is_owner_scoped(
    app,
):
    first_user = ObjectId()
    second_user = ObjectId()

    with app.app_context():
        first = (
            ensure_notification_preferences_for_user(
                first_user,
                now=BASE_TIME,
            )
        )

        second = (
            ensure_notification_preferences_for_user(
                second_user,
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        assert (
            get_notification_preferences_for_user(
                first_user
            )[
                "_id"
            ]
            == first[
                "_id"
            ]
        )

        assert (
            get_notification_preferences_for_user(
                second_user
            )[
                "_id"
            ]
            == second[
                "_id"
            ]
        )

        assert (
            first[
                "_id"
            ]
            != second[
                "_id"
            ]
        )


@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        "not-an-object-id",
        123,
    ],
)
def test_notification_preferences_repository_invalid_user_ids_fail_closed(
    app,
    user_id,
):
    with app.app_context():
        assert (
            get_notification_preferences_for_user(
                user_id
            )
            is None
        )

        assert (
            ensure_notification_preferences_for_user(
                user_id
            )
            is None
        )

        assert (
            update_notification_preferences_for_user(
                user_id,
                disabled_types=[],
            )
            is None
        )


def test_ensure_notification_preferences_is_idempotent(
    app,
):
    user_id = ObjectId()

    first_time = BASE_TIME

    later_time = (
        BASE_TIME
        + timedelta(
            minutes=5
        )
    )

    with app.app_context():
        first = (
            ensure_notification_preferences_for_user(
                user_id,
                now=first_time,
            )
        )

        repeated = (
            ensure_notification_preferences_for_user(
                str(
                    user_id
                ),
                now=later_time,
            )
        )

        assert (
            repeated[
                "_id"
            ]
            == first[
                "_id"
            ]
        )

        assert (
            repeated[
                "created_at"
            ]
            == first[
                "created_at"
            ]
        )

        assert (
            repeated[
                "updated_at"
            ]
            == first[
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


def test_update_notification_preferences_normalizes_and_preserves_creation(
    app,
):
    user_id = ObjectId()

    first_type = (
        _disableable_types()[0]
    )

    with app.app_context():
        original = (
            ensure_notification_preferences_for_user(
                user_id,
                now=BASE_TIME,
            )
        )

        updated = (
            update_notification_preferences_for_user(
                user_id,
                disabled_types=[
                    first_type,
                    f" {first_type.upper()} ",
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        assert (
            updated[
                "_id"
            ]
            == original[
                "_id"
            ]
        )

        assert (
            updated[
                "created_at"
            ]
            == original[
                "created_at"
            ]
        )

        assert (
            updated[
                "disabled_types"
            ]
            == [
                first_type,
            ]
        )

        assert (
            updated[
                "updated_at"
            ]
            != original[
                "updated_at"
            ]
        )


def test_update_notification_preferences_upserts_missing_record(
    app,
):
    user_id = ObjectId()

    first_type = (
        _disableable_types()[0]
    )

    with app.app_context():
        assert (
            get_notification_preferences_for_user(
                user_id
            )
            is None
        )

        updated = (
            update_notification_preferences_for_user(
                user_id,
                disabled_types=[
                    first_type,
                ],
                now=BASE_TIME,
            )
        )

        assert (
            updated[
                "user_id"
            ]
            == user_id
        )

        assert (
            updated[
                "disabled_types"
            ]
            == [
                first_type,
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


def test_repository_rejects_mandatory_notification_disable(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        with pytest.raises(
            ValueError,
            match=(
                "Mandatory notification types "
                "cannot be disabled"
            ),
        ):
            update_notification_preferences_for_user(
                user_id,
                disabled_types=[
                    "security_alert",
                ],
                now=BASE_TIME,
            )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 0
        )


def test_concurrent_notification_preferences_ensure(
    app,
):
    user_id = ObjectId()

    worker_count = 8

    barrier = Barrier(
        worker_count
    )

    def worker(index):
        with app.app_context():
            barrier.wait()

            return (
                ensure_notification_preferences_for_user(
                    user_id,
                    now=(
                        BASE_TIME
                        + timedelta(
                            seconds=index
                        )
                    ),
                )
            )

    with ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:
        results = list(
            executor.map(
                worker,
                range(
                    worker_count
                ),
            )
        )

    ids = {
        result[
            "_id"
        ]
        for result in results
    }

    assert len(
        ids
    ) == 1

    with app.app_context():
        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )

        stored = (
            get_notification_preferences_for_user(
                user_id
            )
        )

        assert (
            stored[
                "_id"
            ]
            in ids
        )

        assert (
            stored[
                "disabled_types"
            ]
            == []
        )


def test_concurrent_notification_preferences_updates(
    app,
):
    user_id = ObjectId()

    first_type, second_type = (
        _disableable_types()[:2]
    )

    submitted_states = [
        [],
        [
            first_type,
        ],
        [
            second_type,
        ],
        sorted(
            [
                first_type,
                second_type,
            ]
        ),
    ]

    worker_count = 8

    barrier = Barrier(
        worker_count
    )

    def worker(index):
        disabled = (
            submitted_states[
                index
                % len(
                    submitted_states
                )
            ]
        )

        with app.app_context():
            barrier.wait()

            return (
                update_notification_preferences_for_user(
                    user_id,
                    disabled_types=disabled,
                    now=(
                        BASE_TIME
                        + timedelta(
                            seconds=index
                        )
                    ),
                )
            )

    with ThreadPoolExecutor(
        max_workers=worker_count
    ) as executor:
        results = list(
            executor.map(
                worker,
                range(
                    worker_count
                ),
            )
        )

    ids = {
        result[
            "_id"
        ]
        for result in results
    }

    assert len(
        ids
    ) == 1

    with app.app_context():
        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": user_id,
                }
            )
            == 1
        )

        stored = (
            get_notification_preferences_for_user(
                user_id
            )
        )

        assert (
            stored[
                "_id"
            ]
            in ids
        )

        assert (
            stored[
                "disabled_types"
            ]
            in submitted_states
        )
