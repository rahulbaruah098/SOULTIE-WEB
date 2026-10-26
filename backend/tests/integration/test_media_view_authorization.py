"""Authorization tests for Soultie profile-media delivery."""

from io import BytesIO
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from bson import ObjectId
from PIL import Image
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.profile_repository import (
    ensure_profile,
)
from app.schemas.match import (
    build_match_document,
)


TEST_DATABASE = (
    "xuoroni_test_media_view_authorization"
)


@pytest.fixture()
def app():
    otp_prefix = (
        "xuoroni:test:media-view:"
        + uuid4().hex
        + ":"
    )

    with TemporaryDirectory(
        prefix="xuoroni-media-view-"
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
                    "xuoroni-media-view-test-"
                    "secret-at-least-32-bytes"
                ),
                "JWT_ISSUER": "xuoroni-test",
                "JWT_AUDIENCE": (
                    "xuoroni-test-client"
                ),
                "JWT_ACCESS_TOKEN_MINUTES": 15,
                "JWT_REFRESH_TOKEN_DAYS": 30,
                "EXPOSE_DEV_OTP": True,
                "DEV_OTP_CODE": "654321",
                "OTP_TTL_SECONDS": 60,
                "OTP_RESEND_COOLDOWN_SECONDS": 1,
                "OTP_MAX_ATTEMPTS": 3,
                "OTP_REQUEST_WINDOW_SECONDS": 60,
                "OTP_MAX_REQUESTS_PER_WINDOW": 5,
                "OTP_KEY_PREFIX": otp_prefix,
                "MEDIA_STORAGE_BACKEND": "local",
                "MEDIA_LOCAL_ROOT": media_root,
                "MEDIA_MAX_PROFILE_ITEMS": 6,
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

            mongo.cx.drop_database(
                TEST_DATABASE
            )


@pytest.fixture()
def client(
    app,
):
    return app.test_client()


def login(
    client,
    phone,
):
    request_response = client.post(
        "/api/v1/auth/request-otp",
        json={
            "phone": phone,
        },
    )

    assert (
        request_response.status_code
        == 200
    )

    verify_response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "phone": phone,
            "otp": "654321",
        },
    )

    assert (
        verify_response.status_code
        == 200
    )

    payload = (
        verify_response.get_json()
    )

    return {
        "user_id": ObjectId(
            payload[
                "user"
            ][
                "id"
            ]
        ),
        "headers": {
            "Authorization": (
                "Bearer "
                + payload[
                    "access_token"
                ]
            )
        },
    }


def set_profile_state(
    user_id,
    *,
    profile_status="active",
    visibility="visible",
    onboarding_status="completed",
):
    profile = ensure_profile(
        user_id
    )

    mongo.db.profiles.update_one(
        {
            "_id": profile[
                "_id"
            ],
        },
        {
            "$set": {
                "profile_status": (
                    profile_status
                ),
                "visibility": (
                    visibility
                ),
                "onboarding_status": (
                    onboarding_status
                ),
                "profile_completion_percent": 100,
            }
        },
    )

    mongo.db.users.update_one(
        {
            "_id": user_id,
        },
        {
            "$set": {
                "onboarding_status": (
                    onboarding_status
                ),
            }
        },
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


def upload_photo(
    client,
    login_data,
    seed=1,
):
    response = client.post(
        "/api/v1/media",
        headers=login_data[
            "headers"
        ],
        data={
            "kind": "profile_photo",
            "file": (
                BytesIO(
                    make_photo_bytes(
                        seed
                    )
                ),
                "profile.png",
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

    return response.get_json()[
        "media"
    ]


def make_video_bytes(
    app,
    tmp_path,
):
    output = (
        tmp_path
        / "media-view-video.mp4"
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


def upload_video(
    client,
    login_data,
    app,
    tmp_path,
):
    response = client.post(
        "/api/v1/media",
        headers=login_data[
            "headers"
        ],
        data={
            "kind": "profile_video",
            "file": (
                BytesIO(
                    make_video_bytes(
                        app,
                        tmp_path,
                    )
                ),
                "profile.mp4",
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

    return response.get_json()[
        "media"
    ]


def create_match(
    first_user_id,
    second_user_id,
    *,
    status="active",
):
    document = build_match_document(
        first_user_id=first_user_id,
        second_user_id=second_user_id,
        matched_by=first_user_id,
    )

    document[
        "status"
    ] = status

    mongo.db.matches.insert_one(
        document
    )


def assert_hidden(
    response,
):
    assert (
        response.status_code
        == 404
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )


def test_owner_can_read_own_media_even_when_profile_disabled(
    client,
    app,
):
    owner = login(
        client,
        "9876543401",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"]
        )

    media = upload_photo(
        client,
        owner,
        1,
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status="disabled",
            visibility="hidden",
        )

    response = client.get(
        media[
            "content_url"
        ],
        headers=owner[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        response.mimetype
        == "image/webp"
    )

    response.close()


def test_visible_active_profile_media_is_viewable_by_other_authenticated_user(
    client,
    app,
):
    owner = login(
        client,
        "9876543402",
    )

    viewer = login(
        client,
        "9876543403",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status="active",
            visibility="visible",
            onboarding_status="completed",
        )

    media = upload_photo(
        client,
        owner,
        2,
    )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    response.close()


def test_hidden_profile_media_is_not_viewable_without_active_match(
    client,
    app,
):
    owner = login(
        client,
        "9876543404",
    )

    viewer = login(
        client,
        "9876543405",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            visibility="hidden",
        )

    media = upload_photo(
        client,
        owner,
        3,
    )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        response
    )


def test_hidden_profile_media_is_viewable_with_active_match(
    client,
    app,
):
    owner = login(
        client,
        "9876543406",
    )

    viewer = login(
        client,
        "9876543407",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            visibility="hidden",
        )

        create_match(
            owner["user_id"],
            viewer["user_id"],
            status="active",
        )

    media = upload_photo(
        client,
        owner,
        4,
    )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    response.close()


def test_unmatched_relationship_does_not_unlock_hidden_media(
    client,
    app,
):
    owner = login(
        client,
        "9876543408",
    )

    viewer = login(
        client,
        "9876543409",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            visibility="hidden",
        )

        create_match(
            owner["user_id"],
            viewer["user_id"],
            status="unmatched",
        )

    media = upload_photo(
        client,
        owner,
        5,
    )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        response
    )


@pytest.mark.parametrize(
    "reverse_block",
    [
        False,
        True,
    ],
)
def test_block_in_either_direction_hides_media(
    client,
    app,
    reverse_block,
):
    owner = login(
        client,
        (
            "9876543410"
            if not reverse_block
            else "9876543412"
        ),
    )

    viewer = login(
        client,
        (
            "9876543411"
            if not reverse_block
            else "9876543413"
        ),
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            visibility="visible",
        )

        blocker_id = (
            owner["user_id"]
            if reverse_block
            else viewer["user_id"]
        )

        blocked_id = (
            viewer["user_id"]
            if reverse_block
            else owner["user_id"]
        )

        mongo.db.blocks.insert_one(
            {
                "blocker_id": blocker_id,
                "blocked_id": blocked_id,
            }
        )

    media = upload_photo(
        client,
        owner,
        (
            6
            if not reverse_block
            else 7
        ),
    )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        response
    )


@pytest.mark.parametrize(
    "profile_status",
    [
        "paused",
        "disabled",
    ],
)
def test_inactive_owner_profile_hides_media_from_other_users(
    client,
    app,
    profile_status,
):
    owner = login(
        client,
        (
            "9876543414"
            if profile_status
            == "paused"
            else "9876543416"
        ),
    )

    viewer = login(
        client,
        (
            "9876543415"
            if profile_status
            == "paused"
            else "9876543417"
        ),
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status="active",
            visibility="visible",
        )

    media = upload_photo(
        client,
        owner,
        8,
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status=(
                profile_status
            ),
            visibility="visible",
        )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        response
    )


def test_incomplete_owner_onboarding_hides_media(
    client,
    app,
):
    owner = login(
        client,
        "9876543418",
    )

    viewer = login(
        client,
        "9876543419",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status="active",
            visibility="visible",
            onboarding_status="completed",
        )

    media = upload_photo(
        client,
        owner,
        9,
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            profile_status="active",
            visibility="visible",
            onboarding_status="in_progress",
        )

    response = client.get(
        media[
            "content_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        response
    )


def test_video_thumbnail_uses_same_hidden_profile_authorization(
    client,
    app,
    tmp_path,
):
    owner = login(
        client,
        "9876543420",
    )

    viewer = login(
        client,
        "9876543421",
    )

    with app.app_context():
        set_profile_state(
            owner["user_id"],
            visibility="hidden",
        )

    media = upload_video(
        client,
        owner,
        app,
        tmp_path,
    )

    blocked_response = client.get(
        media[
            "thumbnail_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert_hidden(
        blocked_response
    )

    with app.app_context():
        create_match(
            owner["user_id"],
            viewer["user_id"],
            status="active",
        )

    allowed_response = client.get(
        media[
            "thumbnail_url"
        ],
        headers=viewer[
            "headers"
        ],
    )

    assert (
        allowed_response.status_code
        == 200
    )

    assert (
        allowed_response.mimetype
        == "image/webp"
    )

    allowed_response.close()
