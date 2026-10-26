"""Tests for Soultie profile media schemas."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
    MEDIA_STATUS_ACTIVE,
    MEDIA_STORAGE_LOCAL,
    PHOTO_MIME_TYPE,
    VIDEO_MIME_TYPE,
    VIDEO_THUMBNAIL_MIME_TYPE,
    build_profile_media_document,
    build_profile_media_reference,
    normalize_media_kind,
    serialize_profile_media,
)


def fixed_now():
    return datetime(
        2026,
        10,
        9,
        5,
        0,
        0,
        tzinfo=timezone.utc,
    )


def valid_sha256():
    return "a" * 64


def build_photo(
    *,
    user_id=None,
    position=0,
    is_primary=True,
):
    return build_profile_media_document(
        user_id=(
            user_id
            or ObjectId()
        ),
        kind=MEDIA_KIND_PROFILE_PHOTO,
        storage_backend=MEDIA_STORAGE_LOCAL,
        storage_key=(
            "profiles/user/photo.webp"
        ),
        mime_type=PHOTO_MIME_TYPE,
        size_bytes=123456,
        width=1080,
        height=1350,
        position=position,
        sha256=valid_sha256(),
        is_primary=is_primary,
        now=fixed_now(),
    )


def build_video(
    *,
    user_id=None,
    position=1,
    is_primary=False,
):
    return build_profile_media_document(
        user_id=(
            user_id
            or ObjectId()
        ),
        kind=MEDIA_KIND_PROFILE_VIDEO,
        storage_backend=MEDIA_STORAGE_LOCAL,
        storage_key=(
            "profiles/user/video.mp4"
        ),
        mime_type=VIDEO_MIME_TYPE,
        size_bytes=654321,
        width=1080,
        height=1920,
        position=position,
        sha256=("b" * 64),
        is_primary=is_primary,
        duration_ms=3800,
        thumbnail_storage_key=(
            "profiles/user/"
            "video.thumbnail.webp"
        ),
        thumbnail_mime_type=(
            VIDEO_THUMBNAIL_MIME_TYPE
        ),
        thumbnail_width=540,
        thumbnail_height=960,
        now=fixed_now(),
    )


def test_build_photo_document():
    user_id = ObjectId()

    document = build_photo(
        user_id=user_id
    )

    assert document["user_id"] == user_id
    assert (
        document["kind"]
        == MEDIA_KIND_PROFILE_PHOTO
    )
    assert (
        document["storage_backend"]
        == MEDIA_STORAGE_LOCAL
    )
    assert (
        document["mime_type"]
        == PHOTO_MIME_TYPE
    )
    assert document["position"] == 0
    assert document["is_primary"] is True
    assert (
        document["status"]
        == MEDIA_STATUS_ACTIVE
    )
    assert document["duration_ms"] is None
    assert (
        document["thumbnail_storage_key"]
        is None
    )
    assert (
        document["created_at"]
        == fixed_now()
    )
    assert (
        document["updated_at"]
        == fixed_now()
    )


def test_build_video_document():
    user_id = ObjectId()

    document = build_video(
        user_id=user_id
    )

    assert document["user_id"] == user_id
    assert (
        document["kind"]
        == MEDIA_KIND_PROFILE_VIDEO
    )
    assert (
        document["mime_type"]
        == VIDEO_MIME_TYPE
    )
    assert document["duration_ms"] == 3800
    assert (
        document["thumbnail_mime_type"]
        == VIDEO_THUMBNAIL_MIME_TYPE
    )
    assert document["thumbnail_width"] == 540
    assert document["thumbnail_height"] == 960


def test_photo_rejects_duration():
    with pytest.raises(
        ValueError,
        match="cannot have duration",
    ):
        build_profile_media_document(
            user_id=ObjectId(),
            kind=MEDIA_KIND_PROFILE_PHOTO,
            storage_backend=MEDIA_STORAGE_LOCAL,
            storage_key=(
                "profiles/user/photo.webp"
            ),
            mime_type=PHOTO_MIME_TYPE,
            size_bytes=100,
            width=100,
            height=100,
            position=0,
            sha256=valid_sha256(),
            duration_ms=1000,
        )


def test_video_requires_duration():
    with pytest.raises(
        ValueError,
        match="duration_ms",
    ):
        build_profile_media_document(
            user_id=ObjectId(),
            kind=MEDIA_KIND_PROFILE_VIDEO,
            storage_backend=MEDIA_STORAGE_LOCAL,
            storage_key=(
                "profiles/user/video.mp4"
            ),
            mime_type=VIDEO_MIME_TYPE,
            size_bytes=100,
            width=100,
            height=100,
            position=0,
            sha256=valid_sha256(),
            thumbnail_storage_key=(
                "profiles/user/"
                "video.thumbnail.webp"
            ),
            thumbnail_mime_type=(
                VIDEO_THUMBNAIL_MIME_TYPE
            ),
            thumbnail_width=100,
            thumbnail_height=100,
        )


def test_video_requires_thumbnail():
    with pytest.raises(
        ValueError,
        match="thumbnail_storage_key",
    ):
        build_profile_media_document(
            user_id=ObjectId(),
            kind=MEDIA_KIND_PROFILE_VIDEO,
            storage_backend=MEDIA_STORAGE_LOCAL,
            storage_key=(
                "profiles/user/video.mp4"
            ),
            mime_type=VIDEO_MIME_TYPE,
            size_bytes=100,
            width=100,
            height=100,
            position=0,
            sha256=valid_sha256(),
            duration_ms=3000,
            thumbnail_storage_key=None,
            thumbnail_mime_type=(
                VIDEO_THUMBNAIL_MIME_TYPE
            ),
            thumbnail_width=100,
            thumbnail_height=100,
        )


def test_invalid_sha256_rejected():
    with pytest.raises(
        ValueError,
        match="64-character",
    ):
        build_profile_media_document(
            user_id=ObjectId(),
            kind=MEDIA_KIND_PROFILE_PHOTO,
            storage_backend=MEDIA_STORAGE_LOCAL,
            storage_key=(
                "profiles/user/photo.webp"
            ),
            mime_type=PHOTO_MIME_TYPE,
            size_bytes=100,
            width=100,
            height=100,
            position=0,
            sha256="not-a-valid-hash",
        )


def test_storage_traversal_rejected():
    with pytest.raises(
        ValueError,
        match="storage_key is invalid",
    ):
        build_profile_media_document(
            user_id=ObjectId(),
            kind=MEDIA_KIND_PROFILE_PHOTO,
            storage_backend=MEDIA_STORAGE_LOCAL,
            storage_key=(
                "../outside/photo.webp"
            ),
            mime_type=PHOTO_MIME_TYPE,
            size_bytes=100,
            width=100,
            height=100,
            position=0,
            sha256=valid_sha256(),
        )


def test_unknown_media_kind_rejected():
    with pytest.raises(
        ValueError,
        match="Unsupported profile media kind",
    ):
        normalize_media_kind(
            "avatar"
        )


def test_profile_media_reference_for_photo():
    media_id = ObjectId()

    document = build_photo()
    document["_id"] = media_id

    reference = (
        build_profile_media_reference(
            document
        )
    )

    assert reference == {
        "media_id": str(
            media_id
        ),
        "kind": (
            MEDIA_KIND_PROFILE_PHOTO
        ),
        "position": 0,
    }


def test_profile_media_reference_for_video():
    media_id = ObjectId()

    document = build_video()
    document["_id"] = media_id

    reference = (
        build_profile_media_reference(
            document
        )
    )

    assert reference == {
        "media_id": str(
            media_id
        ),
        "kind": (
            MEDIA_KIND_PROFILE_VIDEO
        ),
        "position": 1,
        "duration_ms": 3800,
    }


def test_photo_serializer_is_safe():
    media_id = ObjectId()

    document = build_photo()
    document["_id"] = media_id

    serialized = (
        serialize_profile_media(
            document
        )
    )

    assert serialized["id"] == str(
        media_id
    )

    assert serialized["content_url"] == (
        f"/api/v1/media/"
        f"{media_id}/content"
    )

    assert (
        serialized["thumbnail_url"]
        is None
    )

    assert (
        serialized["duration_ms"]
        is None
    )

    assert "storage_key" not in serialized
    assert (
        "storage_backend"
        not in serialized
    )
    assert "sha256" not in serialized
    assert "user_id" not in serialized


def test_video_serializer_has_thumbnail_url():
    media_id = ObjectId()

    document = build_video()
    document["_id"] = media_id

    serialized = (
        serialize_profile_media(
            document
        )
    )

    assert serialized["duration_ms"] == 3800

    assert serialized["thumbnail_url"] == (
        f"/api/v1/media/"
        f"{media_id}/thumbnail"
    )

    assert "storage_key" not in serialized
    assert (
        "thumbnail_storage_key"
        not in serialized
    )


def test_serializer_normalizes_naive_datetime_to_utc():
    media_id = ObjectId()

    document = build_photo()
    document["_id"] = media_id

    document["created_at"] = datetime(
        2026,
        10,
        9,
        5,
        30,
        0,
    )

    serialized = (
        serialize_profile_media(
            document
        )
    )

    assert serialized[
        "created_at"
    ].endswith(
        "+00:00"
    )
