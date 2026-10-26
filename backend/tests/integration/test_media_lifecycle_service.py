"""Integration tests for Soultie profile-media lifecycle services."""

from io import BytesIO
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from bson import ObjectId
from PIL import Image
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.media_repository import (
    count_active_profile_media,
    get_media_by_id,
    get_primary_media,
    list_active_profile_media,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
import app.services.media_service as media_service_module
from app.services.media_service import (
    MediaServiceError,
    delete_profile_media,
    list_profile_media,
    reorder_profile_media,
    set_profile_primary_media,
    upload_profile_media,
)
from app.services.media_storage_service import (
    get_media_storage,
)
from app.services.profile_validation import (
    calculate_profile_completion,
)


TEST_DB = (
    "xuoroni_test_media_lifecycle_service"
)


@pytest.fixture(scope="module")
def app():
    with TemporaryDirectory(
        prefix="xuoroni-media-lifecycle-"
    ) as media_root:
        application = create_app(
            {
                "TESTING": True,
                "AUTO_ENSURE_INDEXES": False,
                "SOCKETIO_MESSAGE_QUEUE": "",
                "MONGO_URI": (
                    "mongodb://localhost:27017/"
                    + TEST_DB
                ),
                "MEDIA_STORAGE_BACKEND": "local",
                "MEDIA_LOCAL_ROOT": media_root,
                "MEDIA_MAX_PROFILE_ITEMS": 6,
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
def clean_state(app):
    with app.app_context():
        mongo.db.profile_media.delete_many(
            {}
        )

        mongo.db.profiles.delete_many(
            {}
        )

    root = Path(
        app.config[
            "MEDIA_LOCAL_ROOT"
        ]
    )

    shutil.rmtree(
        root,
        ignore_errors=True,
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    yield

    with app.app_context():
        mongo.db.profile_media.delete_many(
            {}
        )

        mongo.db.profiles.delete_many(
            {}
        )

    shutil.rmtree(
        root,
        ignore_errors=True,
    )

    root.mkdir(
        parents=True,
        exist_ok=True,
    )


def make_photo_bytes(
    seed=1,
):
    image = Image.new(
        "RGB",
        (
            320,
            400,
        ),
        (
            seed % 256,
            (
                seed * 37
            ) % 256,
            (
                seed * 83
            ) % 256,
        ),
    )

    buffer = BytesIO()

    image.save(
        buffer,
        format="PNG",
    )

    return buffer.getvalue()


def make_video_bytes(
    app,
    tmp_path,
):
    output = (
        tmp_path
        / "lifecycle-video.mp4"
    )

    ffmpeg = str(
        app.config.get(
            "MEDIA_FFMPEG_BINARY",
            "ffmpeg",
        )
    )

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                "color=c=black:"
                "s=320x240:"
                "r=25:"
                "d=3"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(
                output
            ),
        ],
        check=True,
        capture_output=True,
    )

    return output.read_bytes()


def stored_files(
    app,
):
    root = Path(
        app.config[
            "MEDIA_LOCAL_ROOT"
        ]
    )

    if not root.exists():
        return []

    return [
        path
        for path in root.rglob(
            "*"
        )
        if path.is_file()
    ]


def upload_photo(
    user_id,
    seed,
):
    return upload_profile_media(
        user_id,
        kind="profile_photo",
        content=make_photo_bytes(
            seed
        ),
    )


def test_list_profile_media_returns_safe_active_media(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            1,
        )

        upload_photo(
            user_id,
            2,
        )

        result = list_profile_media(
            user_id
        )

        assert len(
            result["media"]
        ) == 2

        assert (
            result[
                "primary_media_id"
            ]
            == result[
                "media"
            ][0]["id"]
        )

        for item in result[
            "media"
        ]:
            assert (
                "storage_key"
                not in item
            )

            assert (
                "thumbnail_storage_key"
                not in item
            )

            assert (
                item[
                    "content_url"
                ]
                == (
                    "/api/v1/media/"
                    f"{item['id']}/content"
                )
            )


def test_set_primary_switches_and_syncs_profile(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            10,
        )

        upload_photo(
            user_id,
            20,
        )

        before = (
            list_active_profile_media(
                user_id
            )
        )

        second_id = before[1][
            "_id"
        ]

        result = (
            set_profile_primary_media(
                user_id,
                second_id,
            )
        )

        primary = get_primary_media(
            user_id
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert (
            primary["_id"]
            == second_id
        )

        assert (
            result[
                "primary_media_id"
            ]
            == str(
                second_id
            )
        )

        assert (
            profile[
                "primary_media_id"
            ]
            == str(
                second_id
            )
        )

        active = (
            list_active_profile_media(
                user_id
            )
        )

        assert sum(
            1
            for item in active
            if item[
                "is_primary"
            ]
        ) == 1

        # Selecting the same primary again is idempotent.
        again = (
            set_profile_primary_media(
                user_id,
                second_id,
            )
        )

        assert (
            again[
                "primary_media_id"
            ]
            == str(
                second_id
            )
        )


def test_set_primary_rejects_unowned_or_missing_media(
    app,
):
    owner_id = ObjectId()
    other_user_id = ObjectId()

    with app.app_context():
        upload_photo(
            other_user_id,
            30,
        )

        other_media = (
            list_active_profile_media(
                other_user_id
            )[0]
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            set_profile_primary_media(
                owner_id,
                other_media["_id"],
            )

        assert (
            exc_info.value.code
            == "MEDIA_NOT_FOUND"
        )

        assert (
            exc_info.value.status_code
            == 404
        )


def test_reorder_mixed_photo_video_media(
    app,
    tmp_path,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            40,
        )

        video_result = (
            upload_profile_media(
                user_id,
                kind="profile_video",
                content=make_video_bytes(
                    app,
                    tmp_path,
                ),
            )
        )

        upload_photo(
            user_id,
            41,
        )

        before = (
            list_active_profile_media(
                user_id
            )
        )

        original_primary_id = str(
            get_primary_media(
                user_id
            )["_id"]
        )

        requested = [
            str(
                before[2]["_id"]
            ),
            video_result[
                "media"
            ][
                "id"
            ],
            str(
                before[0]["_id"]
            ),
        ]

        result = (
            reorder_profile_media(
                user_id,
                requested,
            )
        )

        active = (
            list_active_profile_media(
                user_id
            )
        )

        assert [
            str(
                item["_id"]
            )
            for item in active
        ] == requested

        assert [
            item[
                "position"
            ]
            for item in active
        ] == [
            0,
            1,
            2,
        ]

        assert [
            item["id"]
            for item in result[
                "media"
            ]
        ] == requested

        # Reordering does not silently change primary media.
        assert (
            result[
                "primary_media_id"
            ]
            == original_primary_id
        )


def test_reorder_requires_complete_unique_active_set(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            50,
        )

        upload_photo(
            user_id,
            51,
        )

        original = (
            list_active_profile_media(
                user_id
            )
        )

        original_ids = [
            str(
                item["_id"]
            )
            for item in original
        ]

        with pytest.raises(
            MediaServiceError
        ) as partial_exc:
            reorder_profile_media(
                user_id,
                [
                    original_ids[0],
                ],
            )

        assert (
            partial_exc.value.code
            == "MEDIA_REORDER_INVALID"
        )

        with pytest.raises(
            MediaServiceError
        ) as duplicate_exc:
            reorder_profile_media(
                user_id,
                [
                    original_ids[0],
                    original_ids[0],
                ],
            )

        assert (
            duplicate_exc.value.code
            == "MEDIA_REORDER_INVALID"
        )

        after = (
            list_active_profile_media(
                user_id
            )
        )

        assert [
            str(
                item["_id"]
            )
            for item in after
        ] == original_ids


def test_delete_non_primary_normalizes_positions_and_keeps_primary(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            60,
        )

        upload_photo(
            user_id,
            61,
        )

        upload_photo(
            user_id,
            62,
        )

        active = (
            list_active_profile_media(
                user_id
            )
        )

        original_primary_id = (
            active[0]["_id"]
        )

        target = active[1]

        storage = get_media_storage()

        assert storage.exists(
            target[
                "storage_key"
            ]
        )

        result = (
            delete_profile_media(
                user_id,
                target["_id"],
            )
        )

        remaining = (
            list_active_profile_media(
                user_id
            )
        )

        assert len(
            remaining
        ) == 2

        assert [
            item[
                "position"
            ]
            for item in remaining
        ] == [
            0,
            1,
        ]

        assert (
            get_primary_media(
                user_id
            )["_id"]
            == original_primary_id
        )

        assert (
            result[
                "deleted_media_id"
            ]
            == str(
                target["_id"]
            )
        )

        assert (
            storage.exists(
                target[
                    "storage_key"
                ]
            )
            is False
        )

        deleted = get_media_by_id(
            target["_id"],
            include_deleted=True,
        )

        assert (
            deleted["status"]
            == "deleted"
        )


def test_delete_primary_selects_first_remaining_media(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            70,
        )

        upload_photo(
            user_id,
            71,
        )

        upload_photo(
            user_id,
            72,
        )

        active = (
            list_active_profile_media(
                user_id
            )
        )

        deleted_primary = active[0]
        expected_new_primary = active[1]

        result = (
            delete_profile_media(
                user_id,
                deleted_primary[
                    "_id"
                ],
            )
        )

        remaining = (
            list_active_profile_media(
                user_id
            )
        )

        primary = get_primary_media(
            user_id
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert (
            primary["_id"]
            == expected_new_primary[
                "_id"
            ]
        )

        assert (
            remaining[0][
                "_id"
            ]
            == expected_new_primary[
                "_id"
            ]
        )

        assert (
            result[
                "primary_media_id"
            ]
            == str(
                expected_new_primary[
                    "_id"
                ]
            )
        )

        assert (
            profile[
                "primary_media_id"
            ]
            == str(
                expected_new_primary[
                    "_id"
                ]
            )
        )


def test_delete_video_removes_content_and_thumbnail(
    app,
    tmp_path,
):
    user_id = ObjectId()

    with app.app_context():
        upload_profile_media(
            user_id,
            kind="profile_video",
            content=make_video_bytes(
                app,
                tmp_path,
            ),
        )

        media = (
            list_active_profile_media(
                user_id
            )[0]
        )

        storage = get_media_storage()

        content_key = media[
            "storage_key"
        ]

        thumbnail_key = media[
            "thumbnail_storage_key"
        ]

        assert storage.exists(
            content_key
        )

        assert storage.exists(
            thumbnail_key
        )

        assert len(
            stored_files(
                app
            )
        ) == 2

        delete_profile_media(
            user_id,
            media["_id"],
        )

        assert (
            storage.exists(
                content_key
            )
            is False
        )

        assert (
            storage.exists(
                thumbnail_key
            )
            is False
        )

        assert stored_files(
            app
        ) == []


def test_delete_last_media_clears_profile_and_recalculates_completion(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            80,
        )

        media = (
            list_active_profile_media(
                user_id
            )[0]
        )

        before = get_profile_by_user_id(
            user_id
        )

        before_completion = before[
            "profile_completion_percent"
        ]

        result = (
            delete_profile_media(
                user_id,
                media["_id"],
            )
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 0
        )

        assert profile[
            "media"
        ] == []

        assert (
            profile[
                "primary_media_id"
            ]
            is None
        )

        assert (
            result[
                "primary_media_id"
            ]
            is None
        )

        assert result[
            "media"
        ] == []

        assert (
            profile[
                "profile_completion_percent"
            ]
            == calculate_profile_completion(
                profile
            )
        )

        assert (
            profile[
                "profile_completion_percent"
            ]
            <= before_completion
        )


def test_delete_sync_failure_restores_metadata_order_primary_and_files(
    app,
    monkeypatch,
):
    user_id = ObjectId()

    with app.app_context():
        upload_photo(
            user_id,
            90,
        )

        upload_photo(
            user_id,
            91,
        )

        original = (
            list_active_profile_media(
                user_id
            )
        )

        original_ids = [
            str(
                item["_id"]
            )
            for item in original
        ]

        original_primary_id = str(
            get_primary_media(
                user_id
            )["_id"]
        )

        target = original[1]

        storage = get_media_storage()

        assert storage.exists(
            target[
                "storage_key"
            ]
        )

        files_before = len(
            stored_files(
                app
            )
        )

        real_sync = (
            media_service_module
            ._sync_profile_media_state
        )

        call_count = {
            "value": 0,
        }

        def fail_first_sync(
            sync_user_id,
        ):
            call_count[
                "value"
            ] += 1

            if (
                call_count[
                    "value"
                ]
                == 1
            ):
                raise RuntimeError(
                    "forced sync failure"
                )

            return real_sync(
                sync_user_id
            )

        monkeypatch.setattr(
            media_service_module,
            "_sync_profile_media_state",
            fail_first_sync,
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            delete_profile_media(
                user_id,
                target["_id"],
            )

        assert (
            exc_info.value.code
            == "MEDIA_DELETE_FAILED"
        )

        restored = (
            list_active_profile_media(
                user_id
            )
        )

        assert [
            str(
                item["_id"]
            )
            for item in restored
        ] == original_ids

        assert (
            str(
                get_primary_media(
                    user_id
                )["_id"]
            )
            == original_primary_id
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert [
            item[
                "media_id"
            ]
            for item in profile[
                "media"
            ]
        ] == original_ids

        assert (
            profile[
                "primary_media_id"
            ]
            == original_primary_id
        )

        assert storage.exists(
            target[
                "storage_key"
            ]
        )

        assert (
            len(
                stored_files(
                    app
                )
            )
            == files_before
        )

        restored_target = (
            get_media_by_id(
                target["_id"]
            )
        )

        assert restored_target is not None
        assert (
            restored_target[
                "status"
            ]
            == "active"
        )
