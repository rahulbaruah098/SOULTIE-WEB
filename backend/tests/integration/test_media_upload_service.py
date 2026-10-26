"""Integration tests for Soultie profile media upload orchestration."""

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
    list_active_profile_media,
)
from app.repositories.profile_repository import (
    create_profile,
    get_profile_by_user_id,
)
from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    PHOTO_MIME_TYPE,
    VIDEO_MIME_TYPE,
    VIDEO_THUMBNAIL_MIME_TYPE,
)
import app.services.media_service as media_service_module
from app.services.media_service import (
    MediaServiceError,
    upload_profile_media,
)
from app.services.media_storage_service import (
    get_media_storage,
)
from app.services.profile_validation import (
    calculate_profile_completion,
)


TEST_DB = "xuoroni_test_media_upload_service"


@pytest.fixture(scope="module")
def app():
    with TemporaryDirectory(
        prefix="xuoroni-media-upload-"
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
        / "test-profile-video.mp4"
    )

    ffmpeg = str(
        app.config.get(
            "MEDIA_FFMPEG_BINARY",
            "ffmpeg",
        )
    )

    command = [
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
    ]

    subprocess.run(
        command,
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


def test_photo_upload_persists_and_syncs_profile(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        profile_before = create_profile(
            user_id
        )

        onboarding_before = (
            profile_before.get(
                "onboarding"
            )
        )

        status_before = (
            profile_before.get(
                "onboarding_status"
            )
        )

        result = upload_profile_media(
            user_id,
            kind=MEDIA_KIND_PROFILE_PHOTO,
            content=make_photo_bytes(
                1
            ),
        )

        media = (
            mongo.db.profile_media.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        profile = get_profile_by_user_id(
            user_id
        )

        storage = get_media_storage()

        assert media is not None

        assert (
            media["kind"]
            == MEDIA_KIND_PROFILE_PHOTO
        )

        assert (
            media["mime_type"]
            == PHOTO_MIME_TYPE
        )

        assert media["position"] == 0
        assert media["is_primary"] is True

        assert storage.exists(
            media["storage_key"]
        )

        assert len(
            profile["media"]
        ) == 1

        assert (
            profile["media"][0][
                "media_id"
            ]
            == str(
                media["_id"]
            )
        )

        assert (
            profile[
                "primary_media_id"
            ]
            == str(
                media["_id"]
            )
        )

        assert (
            result[
                "primary_media_id"
            ]
            == str(
                media["_id"]
            )
        )

        assert (
            profile[
                "profile_completion_percent"
            ]
            == calculate_profile_completion(
                profile
            )
        )

        # Uploading media does not itself complete
        # or advance the onboarding step.
        assert (
            profile.get(
                "onboarding_status"
            )
            == status_before
        )

        assert (
            profile.get(
                "onboarding"
            )
            == onboarding_before
        )


def test_second_upload_preserves_primary_and_order(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        upload_profile_media(
            user_id,
            kind=MEDIA_KIND_PROFILE_PHOTO,
            content=make_photo_bytes(
                10
            ),
        )

        upload_profile_media(
            user_id,
            kind=MEDIA_KIND_PROFILE_PHOTO,
            content=make_photo_bytes(
                20
            ),
        )

        media = (
            list_active_profile_media(
                user_id
            )
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert len(media) == 2

        assert [
            item["position"]
            for item in media
        ] == [
            0,
            1,
        ]

        assert (
            media[0]["is_primary"]
            is True
        )

        assert (
            media[1]["is_primary"]
            is False
        )

        assert (
            profile[
                "primary_media_id"
            ]
            == str(
                media[0]["_id"]
            )
        )

        assert [
            item["media_id"]
            for item
            in profile["media"]
        ] == [
            str(
                media[0]["_id"]
            ),
            str(
                media[1]["_id"]
            ),
        ]


def test_video_upload_persists_video_and_thumbnail(
    app,
    tmp_path,
):
    user_id = ObjectId()

    source = make_video_bytes(
        app,
        tmp_path,
    )

    with app.app_context():
        upload_profile_media(
            user_id,
            kind=MEDIA_KIND_PROFILE_VIDEO,
            content=source,
        )

        media = (
            mongo.db.profile_media.find_one(
                {
                    "user_id": user_id,
                }
            )
        )

        profile = get_profile_by_user_id(
            user_id
        )

        storage = get_media_storage()

        assert media is not None

        assert (
            media["kind"]
            == MEDIA_KIND_PROFILE_VIDEO
        )

        assert (
            media["mime_type"]
            == VIDEO_MIME_TYPE
        )

        assert (
            media[
                "thumbnail_mime_type"
            ]
            == VIDEO_THUMBNAIL_MIME_TYPE
        )

        assert (
            0
            < media["duration_ms"]
            <= 4000
        )

        assert storage.exists(
            media["storage_key"]
        )

        assert storage.exists(
            media[
                "thumbnail_storage_key"
            ]
        )

        assert (
            profile["media"][0][
                "duration_ms"
            ]
            == media[
                "duration_ms"
            ]
        )

        assert len(
            stored_files(
                app
            )
        ) == 2


def test_duplicate_upload_is_rejected_without_side_effects(
    app,
):
    user_id = ObjectId()

    content = make_photo_bytes(
        30
    )

    with app.app_context():
        upload_profile_media(
            user_id,
            kind=MEDIA_KIND_PROFILE_PHOTO,
            content=content,
        )

        files_before = len(
            stored_files(
                app
            )
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            upload_profile_media(
                user_id,
                kind=MEDIA_KIND_PROFILE_PHOTO,
                content=content,
            )

        assert (
            exc_info.value.code
            == "MEDIA_DUPLICATE"
        )

        assert (
            exc_info.value.status_code
            == 409
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 1
        )

        assert (
            len(
                stored_files(
                    app
                )
            )
            == files_before
        )


def test_profile_media_limit_is_six(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        for index in range(
            6
        ):
            upload_profile_media(
                user_id,
                kind=MEDIA_KIND_PROFILE_PHOTO,
                content=make_photo_bytes(
                    50 + index
                ),
            )

        assert (
            count_active_profile_media(
                user_id
            )
            == 6
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            upload_profile_media(
                user_id,
                kind=MEDIA_KIND_PROFILE_PHOTO,
                content=make_photo_bytes(
                    99
                ),
            )

        assert (
            exc_info.value.code
            == "MEDIA_LIMIT_REACHED"
        )

        assert (
            exc_info.value.status_code
            == 409
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 6
        )

        assert (
            len(
                stored_files(
                    app
                )
            )
            == 6
        )


def test_disabled_profile_cannot_upload_media(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        create_profile(
            user_id
        )

        mongo.db.profiles.update_one(
            {
                "user_id": user_id,
            },
            {
                "$set": {
                    "profile_status": "disabled",
                }
            },
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            upload_profile_media(
                user_id,
                kind=MEDIA_KIND_PROFILE_PHOTO,
                content=make_photo_bytes(
                    120
                ),
            )

        assert (
            exc_info.value.code
            == "MEDIA_PROFILE_DISABLED"
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 0
        )

        assert stored_files(
            app
        ) == []


def test_invalid_media_kind_is_rejected_without_storage(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            upload_profile_media(
                user_id,
                kind="profile_audio",
                content=make_photo_bytes(
                    130
                ),
            )

        assert (
            exc_info.value.code
            == "MEDIA_INVALID_KIND"
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 0
        )

        assert stored_files(
            app
        ) == []


def test_post_sync_failure_rolls_back_everything(
    app,
    monkeypatch,
):
    user_id = ObjectId()

    with app.app_context():
        create_profile(
            user_id
        )

        monkeypatch.setattr(
            media_service_module,
            "get_owned_media",
            lambda *args, **kwargs: None,
        )

        with pytest.raises(
            MediaServiceError
        ) as exc_info:
            upload_profile_media(
                user_id,
                kind=MEDIA_KIND_PROFILE_PHOTO,
                content=make_photo_bytes(
                    140
                ),
            )

        assert (
            exc_info.value.code
            == "MEDIA_PERSISTENCE_ERROR"
        )

        assert (
            count_active_profile_media(
                user_id
            )
            == 0
        )

        profile = get_profile_by_user_id(
            user_id
        )

        assert (
            profile.get(
                "media"
            )
            == []
        )

        assert (
            profile.get(
                "primary_media_id"
            )
            is None
        )

        assert (
            profile[
                "profile_completion_percent"
            ]
            == calculate_profile_completion(
                profile
            )
        )

        assert stored_files(
            app
        ) == []
