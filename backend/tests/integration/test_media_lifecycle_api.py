"""HTTP integration tests for Soultie profile-media lifecycle API."""

from io import BytesIO
import json
from tempfile import TemporaryDirectory

from bson import ObjectId
from PIL import Image
import pytest
import redis as redis_lib

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo


TEST_DATABASE = (
    "xuoroni_test_media_lifecycle_api"
)

OTP_PREFIX = (
    "xuoroni:test:media-lifecycle-api"
)


@pytest.fixture()
def app():
    with TemporaryDirectory(
        prefix="xuoroni-media-lifecycle-api-"
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
                    "xuoroni-media-lifecycle-api-"
                    "secret-at-least-32-bytes"
                ),
                "OTP_HASH_SECRET": (
                    "xuoroni-media-lifecycle-api-otp-"
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
    phone="9876543210",
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

    token = (
        verify_response.get_json()[
            "access_token"
        ]
    )

    return {
        "Authorization": (
            f"Bearer {token}"
        )
    }


def make_photo_bytes(
    seed,
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
    headers,
    seed,
):
    response = client.post(
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
                f"photo-{seed}.png",
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


def test_list_media_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/media"
    )

    assert (
        response.status_code
        == 401
    )


def test_primary_reorder_and_delete_require_authentication(
    client,
):
    media_id = str(
        ObjectId()
    )

    primary = client.patch(
        (
            "/api/v1/media/"
            f"{media_id}/primary"
        )
    )

    reorder = client.patch(
        "/api/v1/media/reorder",
        json={
            "media_ids": [],
        },
    )

    delete = client.delete(
        (
            "/api/v1/media/"
            f"{media_id}"
        )
    )

    assert primary.status_code == 401
    assert reorder.status_code == 401
    assert delete.status_code == 401


def test_list_media_returns_safe_uploaded_media(
    client,
):
    headers = auth_headers(
        client
    )

    first = upload_photo(
        client,
        headers,
        10,
    )

    second = upload_photo(
        client,
        headers,
        11,
    )

    response = client.get(
        "/api/v1/media",
        headers=headers,
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert [
        item["id"]
        for item in body[
            "media"
        ]
    ] == [
        first["id"],
        second["id"],
    ]

    assert (
        body[
            "primary_media_id"
        ]
        == first["id"]
    )

    serialized = json.dumps(
        body
    )

    assert "storage_key" not in serialized
    assert (
        "thumbnail_storage_key"
        not in serialized
    )


def test_set_primary_media_over_http(
    client,
):
    headers = auth_headers(
        client
    )

    first = upload_photo(
        client,
        headers,
        20,
    )

    second = upload_photo(
        client,
        headers,
        21,
    )

    response = client.patch(
        (
            "/api/v1/media/"
            f"{second['id']}/primary"
        ),
        headers=headers,
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert (
        body[
            "primary_media_id"
        ]
        == second["id"]
    )

    by_id = {
        item["id"]: item
        for item in body[
            "media"
        ]
    }

    assert (
        by_id[
            first["id"]
        ][
            "is_primary"
        ]
        is False
    )

    assert (
        by_id[
            second["id"]
        ][
            "is_primary"
        ]
        is True
    )


def test_reorder_media_over_http(
    client,
):
    headers = auth_headers(
        client
    )

    first = upload_photo(
        client,
        headers,
        30,
    )

    second = upload_photo(
        client,
        headers,
        31,
    )

    third = upload_photo(
        client,
        headers,
        32,
    )

    requested = [
        third["id"],
        first["id"],
        second["id"],
    ]

    response = client.patch(
        "/api/v1/media/reorder",
        headers=headers,
        json={
            "media_ids": requested,
        },
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert [
        item["id"]
        for item in body[
            "media"
        ]
    ] == requested

    assert [
        item[
            "position"
        ]
        for item in body[
            "media"
        ]
    ] == [
        0,
        1,
        2,
    ]

    assert (
        body[
            "primary_media_id"
        ]
        == first["id"]
    )


def test_reorder_rejects_incomplete_set(
    client,
):
    headers = auth_headers(
        client
    )

    first = upload_photo(
        client,
        headers,
        40,
    )

    upload_photo(
        client,
        headers,
        41,
    )

    response = client.patch(
        "/api/v1/media/reorder",
        headers=headers,
        json={
            "media_ids": [
                first["id"],
            ],
        },
    )

    assert (
        response.status_code
        == 400
    )

    body = response.get_json()

    assert (
        body["error"]["code"]
        == "MEDIA_REORDER_INVALID"
    )


def test_user_cannot_modify_another_users_media(
    client,
):
    owner_headers = auth_headers(
        client,
        "9876543210",
    )

    other_headers = auth_headers(
        client,
        "9876543211",
    )

    owner_media = upload_photo(
        client,
        owner_headers,
        50,
    )

    primary_response = client.patch(
        (
            "/api/v1/media/"
            f"{owner_media['id']}/primary"
        ),
        headers=other_headers,
    )

    delete_response = client.delete(
        (
            "/api/v1/media/"
            f"{owner_media['id']}"
        ),
        headers=other_headers,
    )

    assert (
        primary_response.status_code
        == 404
    )

    assert (
        delete_response.status_code
        == 404
    )

    assert (
        primary_response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )

    assert (
        delete_response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )

    owner_list = client.get(
        "/api/v1/media",
        headers=owner_headers,
    )

    assert (
        owner_list.status_code
        == 200
    )

    assert [
        item["id"]
        for item in owner_list.get_json()[
            "media"
        ]
    ] == [
        owner_media["id"],
    ]


def test_delete_media_removes_it_from_list_and_content_route(
    client,
):
    headers = auth_headers(
        client
    )

    first = upload_photo(
        client,
        headers,
        60,
    )

    second = upload_photo(
        client,
        headers,
        61,
    )

    response = client.delete(
        (
            "/api/v1/media/"
            f"{first['id']}"
        ),
        headers=headers,
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert (
        body[
            "deleted_media_id"
        ]
        == first["id"]
    )

    assert [
        item["id"]
        for item in body[
            "media"
        ]
    ] == [
        second["id"],
    ]

    assert (
        body[
            "primary_media_id"
        ]
        == second["id"]
    )

    listing = client.get(
        "/api/v1/media",
        headers=headers,
    )

    assert (
        listing.status_code
        == 200
    )

    assert [
        item["id"]
        for item in listing.get_json()[
            "media"
        ]
    ] == [
        second["id"],
    ]

    deleted_content = client.get(
        first[
            "content_url"
        ],
        headers=headers,
    )

    assert (
        deleted_content.status_code
        == 404
    )

    assert (
        deleted_content.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )


def test_delete_last_media_clears_primary(
    client,
):
    headers = auth_headers(
        client
    )

    media = upload_photo(
        client,
        headers,
        70,
    )

    response = client.delete(
        (
            "/api/v1/media/"
            f"{media['id']}"
        ),
        headers=headers,
    )

    assert (
        response.status_code
        == 200
    )

    body = response.get_json()

    assert body[
        "media"
    ] == []

    assert (
        body[
            "primary_media_id"
        ]
        is None
    )


def test_missing_media_lifecycle_operations_return_not_found(
    client,
):
    headers = auth_headers(
        client
    )

    missing_id = str(
        ObjectId()
    )

    primary_response = client.patch(
        (
            "/api/v1/media/"
            f"{missing_id}/primary"
        ),
        headers=headers,
    )

    delete_response = client.delete(
        (
            "/api/v1/media/"
            f"{missing_id}"
        ),
        headers=headers,
    )

    assert (
        primary_response.status_code
        == 404
    )

    assert (
        delete_response.status_code
        == 404
    )

    assert (
        primary_response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )

    assert (
        delete_response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MEDIA_NOT_FOUND"
    )
