"""Integration tests for Soultie authenticated profile media API."""

from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from bson import ObjectId
from PIL import Image
import pytest
import redis as redis_lib

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo


TEST_DATABASE = (
    "xuoroni_test_media_api"
)

OTP_PREFIX = (
    "xuoroni:test:media-api"
)


@pytest.fixture()
def app():
    with TemporaryDirectory(
        prefix="xuoroni-media-api-"
    ) as media_root:
        application = create_app(
            {
                "TESTING": True,
                "AUTO_ENSURE_INDEXES": False,
                "SOCKETIO_MESSAGE_QUEUE": "",
                "MONGO_URI": (
                    "mongodb://localhost:27017/"
                    + TEST_DATABASE
                ),
                "JWT_SECRET_KEY": (
                    "xuoroni-media-api-test-"
                    "secret-at-least-32-bytes"
                ),
                "OTP_HASH_SECRET": (
                    "xuoroni-media-api-otp-"
                    "secret-at-least-32-bytes"
                ),
                "OTP_KEY_PREFIX": (
                    OTP_PREFIX
                ),
                "EXPOSE_DEV_OTP": True,
                "DEV_OTP_CODE": "654321",
                "OTP_RESEND_COOLDOWN_SECONDS": 1,
                "MEDIA_STORAGE_BACKEND": "local",
                "MEDIA_LOCAL_ROOT": media_root,
                "MEDIA_MAX_PROFILE_ITEMS": 6,
            }
        )

        redis_client = (
            redis_lib.Redis.from_url(
                application.config[
                    "REDIS_URL"
                ],
                decode_responses=True,
            )
        )

        with application.app_context():
            mongo.cx.drop_database(
                TEST_DATABASE
            )

            ensure_indexes(
                mongo.db
            )

            keys = list(
                redis_client.scan_iter(
                    f"{OTP_PREFIX}*"
                )
            )

            if keys:
                redis_client.delete(
                    *keys
                )

            yield application

            mongo.cx.drop_database(
                TEST_DATABASE
            )

            keys = list(
                redis_client.scan_iter(
                    f"{OTP_PREFIX}*"
                )
            )

            if keys:
                redis_client.delete(
                    *keys
                )


@pytest.fixture()
def client(app):
    return app.test_client()


def auth_headers(
    client,
):
    request_response = client.post(
        "/api/v1/auth/request-otp",
        json={
            "phone": "9876543210",
        },
    )

    assert (
        request_response.status_code
        == 200
    )

    verify_response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "phone": "9876543210",
            "otp": "654321",
        },
    )

    assert (
        verify_response.status_code
        == 200
    )

    access_token = (
        verify_response.get_json()[
            "access_token"
        ]
    )

    return {
        "Authorization": (
            f"Bearer {access_token}"
        )
    }


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
        / "media-api-test.mp4"
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


def upload_photo(
    client,
    headers,
    *,
    seed=1,
):
    return client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_photo",
            "file": (
                BytesIO(
                    make_photo_bytes(
                        seed
                    )
                ),
                "client-photo.png",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )


def test_media_upload_requires_authentication(
    client,
):
    response = client.post(
        "/api/v1/media",
        data={
            "kind": "profile_photo",
            "file": (
                BytesIO(
                    make_photo_bytes(
                        1
                    )
                ),
                "photo.png",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert (
        response.status_code
        == 401
    )


def test_media_content_requires_authentication(
    client,
):
    response = client.get(
        (
            "/api/v1/media/"
            f"{ObjectId()}/content"
        )
    )

    assert (
        response.status_code
        == 401
    )


def test_upload_requires_multipart_file(
    client,
):
    headers = auth_headers(
        client
    )

    response = client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_photo",
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert (
        response.status_code
        == 400
    )

    body = response.get_json()

    assert (
        body["error"]["code"]
        == "MEDIA_FILE_REQUIRED"
    )


def test_authenticated_photo_upload_and_content(
    client,
):
    headers = auth_headers(
        client
    )

    response = upload_photo(
        client,
        headers,
        seed=10,
    )

    assert (
        response.status_code
        == 201
    )

    body = response.get_json()
    media = body["media"]

    assert (
        media["kind"]
        == "profile_photo"
    )

    assert (
        media["mime_type"]
        == "image/webp"
    )

    assert media["is_primary"] is True

    assert (
        media["content_url"]
        == (
            "/api/v1/media/"
            f"{media['id']}/content"
        )
    )

    # API clients must never receive internal
    # object-storage/filesystem details.
    serialized = json.dumps(
        body
    )

    assert "storage_key" not in serialized
    assert (
        "thumbnail_storage_key"
        not in serialized
    )
    assert "MEDIA_LOCAL_ROOT" not in serialized
    assert "profile_media/" not in serialized

    content_response = client.get(
        media["content_url"],
        headers=headers,
    )

    assert (
        content_response.status_code
        == 200
    )

    assert (
        content_response.mimetype
        == "image/webp"
    )

    assert (
        content_response.headers[
            "X-Content-Type-Options"
        ]
        == "nosniff"
    )

    assert (
        "private"
        in content_response.headers[
            "Cache-Control"
        ]
    )

    assert len(
        content_response.data
    ) > 0

    content_response.close()


def test_duplicate_upload_returns_conflict(
    client,
):
    headers = auth_headers(
        client
    )

    content = make_photo_bytes(
        20
    )

    first = client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_photo",
            "file": (
                BytesIO(
                    content
                ),
                "first.png",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert first.status_code == 201

    duplicate = client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_photo",
            "file": (
                BytesIO(
                    content
                ),
                "different-client-name.png",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert (
        duplicate.status_code
        == 409
    )

    body = duplicate.get_json()

    assert (
        body["error"]["code"]
        == "MEDIA_DUPLICATE"
    )


def test_video_upload_content_and_thumbnail(
    client,
    app,
    tmp_path,
):
    headers = auth_headers(
        client
    )

    video = make_video_bytes(
        app,
        tmp_path,
    )

    response = client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_video",
            "file": (
                BytesIO(
                    video
                ),
                "client-video.mp4",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert (
        response.status_code
        == 201
    )

    media = response.get_json()[
        "media"
    ]

    assert (
        media["kind"]
        == "profile_video"
    )

    assert (
        media["mime_type"]
        == "video/mp4"
    )

    assert (
        0
        < media["duration_ms"]
        <= 4000
    )

    assert (
        media["thumbnail_url"]
        == (
            "/api/v1/media/"
            f"{media['id']}/thumbnail"
        )
    )

    content_response = client.get(
        media["content_url"],
        headers=headers,
    )

    assert (
        content_response.status_code
        == 200
    )

    assert (
        content_response.mimetype
        == "video/mp4"
    )

    thumbnail_response = client.get(
        media["thumbnail_url"],
        headers=headers,
    )

    assert (
        thumbnail_response.status_code
        == 200
    )

    assert (
        thumbnail_response.mimetype
        == "image/webp"
    )

    assert len(
        thumbnail_response.data
    ) > 0

    content_response.close()
    thumbnail_response.close()


def test_video_thumbnail_requires_authentication(
    client,
    app,
    tmp_path,
):
    headers = auth_headers(
        client
    )

    video = make_video_bytes(
        app,
        tmp_path,
    )

    response = client.post(
        "/api/v1/media",
        headers=headers,
        data={
            "kind": "profile_video",
            "file": (
                BytesIO(
                    video
                ),
                "video.mp4",
            ),
        },
        content_type=(
            "multipart/form-data"
        ),
    )

    assert response.status_code == 201

    thumbnail_url = (
        response.get_json()[
            "media"
        ][
            "thumbnail_url"
        ]
    )

    unauthorized = client.get(
        thumbnail_url
    )

    assert (
        unauthorized.status_code
        == 401
    )


def test_photo_thumbnail_returns_not_found(
    client,
):
    headers = auth_headers(
        client
    )

    response = upload_photo(
        client,
        headers,
        seed=30,
    )

    assert response.status_code == 201

    media_id = response.get_json()[
        "media"
    ][
        "id"
    ]

    thumbnail_response = client.get(
        (
            "/api/v1/media/"
            f"{media_id}/thumbnail"
        ),
        headers=headers,
    )

    assert (
        thumbnail_response.status_code
        == 404
    )

    body = (
        thumbnail_response.get_json()
    )

    assert (
        body["error"]["code"]
        == "MEDIA_THUMBNAIL_NOT_FOUND"
    )


def test_unknown_media_returns_not_found(
    client,
):
    headers = auth_headers(
        client
    )

    response = client.get(
        (
            "/api/v1/media/"
            f"{ObjectId()}/content"
        ),
        headers=headers,
    )

    assert (
        response.status_code
        == 404
    )

    body = response.get_json()

    assert (
        body["error"]["code"]
        == "MEDIA_NOT_FOUND"
    )
