"""Tests for Soultie local profile media storage."""

from pathlib import Path

import pytest

from app.schemas.media import (
    MEDIA_KIND_PROFILE_PHOTO,
    MEDIA_KIND_PROFILE_VIDEO,
)
from app.services.media_storage_service import (
    LocalMediaStorage,
    MediaStorageError,
    build_profile_media_storage_key,
    build_video_thumbnail_storage_key,
    normalize_storage_key,
)


def test_photo_storage_keys_are_random_and_webp():
    first = build_profile_media_storage_key(
        "user123",
        MEDIA_KIND_PROFILE_PHOTO,
    )

    second = build_profile_media_storage_key(
        "user123",
        MEDIA_KIND_PROFILE_PHOTO,
    )

    assert first != second
    assert first.startswith(
        "profiles/user123/"
    )
    assert first.endswith(
        ".webp"
    )


def test_video_storage_keys_are_random_and_mp4():
    first = build_profile_media_storage_key(
        "user123",
        MEDIA_KIND_PROFILE_VIDEO,
    )

    second = build_profile_media_storage_key(
        "user123",
        MEDIA_KIND_PROFILE_VIDEO,
    )

    assert first != second
    assert first.startswith(
        "profiles/user123/"
    )
    assert first.endswith(
        ".mp4"
    )


def test_video_thumbnail_key_is_webp():
    key = build_video_thumbnail_storage_key(
        "user123"
    )

    assert key.startswith(
        "profiles/user123/"
    )
    assert key.endswith(
        ".thumbnail.webp"
    )


def test_invalid_user_storage_component_rejected():
    with pytest.raises(
        MediaStorageError,
        match="Invalid user identifier",
    ):
        build_profile_media_storage_key(
            "../another-user",
            MEDIA_KIND_PROFILE_PHOTO,
        )


def test_local_storage_save_read_and_exists(
    tmp_path,
):
    root = (
        tmp_path
        / "profile_media"
    )

    storage = LocalMediaStorage(
        root
    )

    key = (
        "profiles/user123/"
        "photo.webp"
    )

    content = (
        b"xuoroni-media-test"
    )

    returned_key = storage.save(
        key,
        content,
    )

    assert returned_key == key

    assert storage.exists(
        key
    ) is True

    assert storage.read(
        key
    ) == content

    internal_path = (
        storage.get_internal_path(
            key
        )
    )

    assert isinstance(
        internal_path,
        Path,
    )

    assert (
        internal_path.read_bytes()
        == content
    )

    assert (
        root.resolve()
        in internal_path.parents
    )


def test_local_storage_delete(
    tmp_path,
):
    storage = LocalMediaStorage(
        tmp_path
        / "profile_media"
    )

    key = (
        "profiles/user123/"
        "photo.webp"
    )

    storage.save(
        key,
        b"test-content",
    )

    assert storage.delete(
        key
    ) is True

    assert storage.exists(
        key
    ) is False

    assert storage.delete(
        key
    ) is False


def test_storage_traversal_rejected(
    tmp_path,
):
    storage = LocalMediaStorage(
        tmp_path
        / "profile_media"
    )

    with pytest.raises(
        MediaStorageError,
        match="Invalid storage key",
    ):
        storage.save(
            "../outside.webp",
            b"content",
        )


def test_empty_content_rejected(
    tmp_path,
):
    storage = LocalMediaStorage(
        tmp_path
        / "profile_media"
    )

    with pytest.raises(
        MediaStorageError,
        match="empty media content",
    ):
        storage.save(
            (
                "profiles/user123/"
                "empty.webp"
            ),
            b"",
        )


def test_non_bytes_content_rejected(
    tmp_path,
):
    storage = LocalMediaStorage(
        tmp_path
        / "profile_media"
    )

    with pytest.raises(
        MediaStorageError,
        match="must be bytes",
    ):
        storage.save(
            (
                "profiles/user123/"
                "invalid.webp"
            ),
            "not-bytes",
        )


def test_atomic_replacement_leaves_no_temp_files(
    tmp_path,
):
    root = (
        tmp_path
        / "profile_media"
    )

    storage = LocalMediaStorage(
        root
    )

    key = (
        "profiles/user123/"
        "photo.webp"
    )

    storage.save(
        key,
        b"version-one",
    )

    storage.save(
        key,
        b"version-two",
    )

    assert storage.read(
        key
    ) == b"version-two"

    temporary_files = list(
        root.rglob(
            ".xuoroni-media-*.tmp"
        )
    )

    assert temporary_files == []


def test_drive_style_storage_key_rejected():
    with pytest.raises(
        MediaStorageError,
        match="Invalid storage key",
    ):
        normalize_storage_key(
            "C:/outside/photo.webp"
        )
