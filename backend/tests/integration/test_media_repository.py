"""Integration tests for Soultie profile media repository."""

from uuid import uuid4

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.media_repository import (
    count_active_profile_media,
    create_profile_media,
    delete_media_record,
    find_active_duplicate_by_hash,
    get_media_by_id,
    get_next_media_position,
    get_owned_media,
    get_primary_media,
    list_active_profile_media,
    mark_media_deleted,
    normalize_media_positions,
    restore_media_active,
    set_primary_media,
    update_media_positions,
)
from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    MEDIA_STATUS_ACTIVE,
    MEDIA_STATUS_DELETED,
    MEDIA_STORAGE_LOCAL,
    PHOTO_MIME_TYPE,
    VIDEO_MIME_TYPE,
    VIDEO_THUMBNAIL_MIME_TYPE,
)


TEST_DB = "xuoroni_test_media_repository"


@pytest.fixture(scope="module")
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DB
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DB
        )

        ensure_indexes(
            mongo.db
        )

    yield application

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DB
        )


@pytest.fixture(autouse=True)
def clean_media(app):
    with app.app_context():
        mongo.db.profile_media.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.profile_media.delete_many(
            {}
        )


def unique_key(
    suffix,
):
    return (
        "profiles/test-user/"
        f"{uuid4().hex}{suffix}"
    )


def create_photo(
    *,
    user_id,
    position=0,
    is_primary=False,
    sha256=None,
):
    return create_profile_media(
        user_id=user_id,
        kind=MEDIA_KIND_PROFILE_PHOTO,
        storage_backend=MEDIA_STORAGE_LOCAL,
        storage_key=unique_key(
            ".webp"
        ),
        mime_type=PHOTO_MIME_TYPE,
        size_bytes=12345,
        width=1080,
        height=1350,
        position=position,
        sha256=(
            sha256
            or uuid4().hex * 2
        ),
        is_primary=is_primary,
    )


def create_video(
    *,
    user_id,
    position=0,
    is_primary=False,
    sha256=None,
):
    return create_profile_media(
        user_id=user_id,
        kind=MEDIA_KIND_PROFILE_VIDEO,
        storage_backend=MEDIA_STORAGE_LOCAL,
        storage_key=unique_key(
            ".mp4"
        ),
        mime_type=VIDEO_MIME_TYPE,
        size_bytes=54321,
        width=1080,
        height=1920,
        position=position,
        sha256=(
            sha256
            or uuid4().hex * 2
        ),
        is_primary=is_primary,
        duration_ms=3800,
        thumbnail_storage_key=(
            unique_key(
                ".thumbnail.webp"
            )
        ),
        thumbnail_mime_type=(
            VIDEO_THUMBNAIL_MIME_TYPE
        ),
        thumbnail_width=540,
        thumbnail_height=960,
    )


def test_create_photo_and_fetch(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        created = create_photo(
            user_id=user_id,
            is_primary=True,
        )

        fetched = get_media_by_id(
            created["_id"]
        )

        owned = get_owned_media(
            user_id,
            created["_id"],
        )

        assert fetched is not None
        assert owned is not None
        assert fetched["_id"] == created["_id"]
        assert fetched["user_id"] == user_id
        assert (
            fetched["kind"]
            == MEDIA_KIND_PROFILE_PHOTO
        )
        assert (
            fetched["status"]
            == MEDIA_STATUS_ACTIVE
        )
        assert fetched["is_primary"] is True


def test_create_video_persists_video_metadata(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        created = create_video(
            user_id=user_id,
            position=1,
        )

        fetched = get_media_by_id(
            created["_id"]
        )

        assert fetched is not None
        assert (
            fetched["kind"]
            == MEDIA_KIND_PROFILE_VIDEO
        )
        assert fetched["duration_ms"] == 3800
        assert (
            fetched["thumbnail_storage_key"]
        )
        assert (
            fetched["thumbnail_mime_type"]
            == VIDEO_THUMBNAIL_MIME_TYPE
        )


def test_list_count_and_next_position(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        first = create_photo(
            user_id=user_id,
            position=0,
            is_primary=True,
        )

        second = create_video(
            user_id=user_id,
            position=1,
        )

        media = list_active_profile_media(
            user_id
        )

        assert [
            item["_id"]
            for item in media
        ] == [
            first["_id"],
            second["_id"],
        ]

        assert (
            count_active_profile_media(
                user_id
            )
            == 2
        )

        assert (
            get_next_media_position(
                user_id
            )
            == 2
        )


def test_duplicate_active_hash_rejected(
    app,
):
    user_id = ObjectId()
    duplicate_hash = "a" * 64

    with app.app_context():
        first = create_photo(
            user_id=user_id,
            sha256=duplicate_hash,
        )

        duplicate = (
            find_active_duplicate_by_hash(
                user_id,
                duplicate_hash,
            )
        )

        assert duplicate is not None
        assert duplicate["_id"] == first["_id"]

        with pytest.raises(
            DuplicateKeyError
        ):
            create_photo(
                user_id=user_id,
                position=1,
                sha256=duplicate_hash,
            )


def test_deleted_hash_can_be_uploaded_again(
    app,
):
    user_id = ObjectId()
    duplicate_hash = "b" * 64

    with app.app_context():
        original = create_photo(
            user_id=user_id,
            sha256=duplicate_hash,
        )

        deleted = mark_media_deleted(
            user_id,
            original["_id"],
        )

        assert (
            deleted["status"]
            == MEDIA_STATUS_DELETED
        )

        replacement = create_photo(
            user_id=user_id,
            sha256=duplicate_hash,
        )

        assert (
            replacement["_id"]
            != original["_id"]
        )


def test_only_one_active_primary_allowed(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        create_photo(
            user_id=user_id,
            position=0,
            is_primary=True,
        )

        with pytest.raises(
            DuplicateKeyError
        ):
            create_photo(
                user_id=user_id,
                position=1,
                is_primary=True,
            )


def test_set_primary_switches_primary(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        first = create_photo(
            user_id=user_id,
            position=0,
            is_primary=True,
        )

        second = create_video(
            user_id=user_id,
            position=1,
        )

        result = set_primary_media(
            user_id,
            second["_id"],
        )

        assert result is not None
        assert result["_id"] == second["_id"]
        assert result["is_primary"] is True

        refreshed_first = get_media_by_id(
            first["_id"]
        )

        assert (
            refreshed_first["is_primary"]
            is False
        )

        primary = get_primary_media(
            user_id
        )

        assert primary is not None
        assert primary["_id"] == second["_id"]


def test_set_primary_rejects_foreign_media(
    app,
):
    owner_id = ObjectId()
    other_user_id = ObjectId()

    with app.app_context():
        media = create_photo(
            user_id=owner_id,
            is_primary=True,
        )

        result = set_primary_media(
            other_user_id,
            media["_id"],
        )

        assert result is None


def test_reorder_updates_positions(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        first = create_photo(
            user_id=user_id,
            position=0,
            is_primary=True,
        )

        second = create_photo(
            user_id=user_id,
            position=1,
        )

        third = create_video(
            user_id=user_id,
            position=2,
        )

        result = update_media_positions(
            user_id,
            [
                third["_id"],
                first["_id"],
                second["_id"],
            ],
        )

        assert result is True

        ordered = list_active_profile_media(
            user_id
        )

        assert [
            item["_id"]
            for item in ordered
        ] == [
            third["_id"],
            first["_id"],
            second["_id"],
        ]

        assert [
            item["position"]
            for item in ordered
        ] == [
            0,
            1,
            2,
        ]


def test_reorder_rejects_duplicate_ids(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        media = create_photo(
            user_id=user_id,
        )

        result = update_media_positions(
            user_id,
            [
                media["_id"],
                media["_id"],
            ],
        )

        assert result is False


def test_reorder_rejects_foreign_media(
    app,
):
    user_id = ObjectId()
    other_user_id = ObjectId()

    with app.app_context():
        mine = create_photo(
            user_id=user_id,
            position=0,
        )

        foreign = create_photo(
            user_id=other_user_id,
            position=0,
        )

        result = update_media_positions(
            user_id,
            [
                mine["_id"],
                foreign["_id"],
            ],
        )

        assert result is False


def test_soft_delete_hides_media_by_default(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        media = create_photo(
            user_id=user_id,
            is_primary=True,
        )

        deleted = mark_media_deleted(
            user_id,
            media["_id"],
        )

        assert deleted is not None
        assert (
            deleted["status"]
            == MEDIA_STATUS_DELETED
        )
        assert deleted["is_primary"] is False
        assert deleted.get(
            "deleted_at"
        ) is not None

        assert (
            get_media_by_id(
                media["_id"]
            )
            is None
        )

        historical = get_media_by_id(
            media["_id"],
            include_deleted=True,
        )

        assert historical is not None
        assert (
            historical["status"]
            == MEDIA_STATUS_DELETED
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 0
        )


def test_restore_media_for_internal_rollback(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        media = create_photo(
            user_id=user_id,
        )

        mark_media_deleted(
            user_id,
            media["_id"],
        )

        restored = restore_media_active(
            user_id,
            media["_id"],
            is_primary=True,
        )

        assert restored is not None
        assert (
            restored["status"]
            == MEDIA_STATUS_ACTIVE
        )
        assert restored["is_primary"] is True
        assert "deleted_at" not in restored


def test_normalize_positions_closes_gaps(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        first = create_photo(
            user_id=user_id,
            position=0,
            is_primary=True,
        )

        second = create_photo(
            user_id=user_id,
            position=1,
        )

        third = create_video(
            user_id=user_id,
            position=2,
        )

        mark_media_deleted(
            user_id,
            second["_id"],
        )

        normalized = normalize_media_positions(
            user_id
        )

        assert [
            item["_id"]
            for item in normalized
        ] == [
            first["_id"],
            third["_id"],
        ]

        assert [
            item["position"]
            for item in normalized
        ] == [
            0,
            1,
        ]


def test_delete_media_record_for_rollback(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        media = create_photo(
            user_id=user_id,
        )

        deleted = delete_media_record(
            media["_id"],
            user_id=user_id,
        )

        assert deleted is True

        assert (
            get_media_by_id(
                media["_id"],
                include_deleted=True,
            )
            is None
        )

        assert (
            delete_media_record(
                media["_id"],
                user_id=user_id,
            )
            is False
        )
