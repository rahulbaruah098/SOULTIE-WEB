"""Canonical schemas and serializers for Soultie profile media."""

from datetime import (
    date,
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId


MEDIA_KIND_PROFILE_PHOTO = "profile_photo"
MEDIA_KIND_PROFILE_VIDEO = "profile_video"

PROFILE_MEDIA_KINDS = frozenset(
    {
        MEDIA_KIND_PROFILE_PHOTO,
        MEDIA_KIND_PROFILE_VIDEO,
    }
)

MEDIA_STATUS_ACTIVE = "active"
MEDIA_STATUS_DELETED = "deleted"

MEDIA_STATUSES = frozenset(
    {
        MEDIA_STATUS_ACTIVE,
        MEDIA_STATUS_DELETED,
    }
)

MEDIA_STORAGE_LOCAL = "local"

SUPPORTED_MEDIA_STORAGE_BACKENDS = frozenset(
    {
        MEDIA_STORAGE_LOCAL,
    }
)

PHOTO_MIME_TYPE = "image/webp"
VIDEO_MIME_TYPE = "video/mp4"
VIDEO_THUMBNAIL_MIME_TYPE = "image/webp"


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
    *,
    field_name="media_id",
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(value)
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            f"Invalid {field_name}."
        ) from None


def normalize_media_kind(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Media kind is required."
        )

    normalized = (
        value.strip().lower()
    )

    if (
        normalized
        not in PROFILE_MEDIA_KINDS
    ):
        raise ValueError(
            "Unsupported profile media kind."
        )

    return normalized


def normalize_media_status(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Media status is required."
        )

    normalized = (
        value.strip().lower()
    )

    if normalized not in MEDIA_STATUSES:
        raise ValueError(
            "Unsupported media status."
        )

    return normalized


def normalize_storage_backend(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Storage backend is required."
        )

    normalized = (
        value.strip().lower()
    )

    if (
        normalized
        not in SUPPORTED_MEDIA_STORAGE_BACKENDS
    ):
        raise ValueError(
            "Unsupported media storage backend."
        )

    return normalized


def _positive_int(
    value,
    *,
    field_name,
    allow_zero=False,
):
    if isinstance(
        value,
        bool,
    ):
        raise ValueError(
            f"{field_name} must be an integer."
        )

    try:
        parsed = int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            f"{field_name} must be an integer."
        ) from None

    minimum = (
        0
        if allow_zero
        else 1
    )

    if parsed < minimum:
        raise ValueError(
            (
                f"{field_name} must be "
                f"{minimum} or greater."
            )
        )

    return parsed


def _normalize_sha256(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "sha256 is required."
        )

    normalized = (
        value.strip().lower()
    )

    if (
        len(normalized) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character in normalized
        )
    ):
        raise ValueError(
            "sha256 must be a 64-character hexadecimal digest."
        )

    return normalized


def _normalize_storage_key(
    value,
    *,
    field_name,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            f"{field_name} is required."
        )

    normalized = (
        value.strip()
        .replace("\\", "/")
    )

    if not normalized:
        raise ValueError(
            f"{field_name} is required."
        )

    if (
        normalized.startswith("/")
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
        or ":" in normalized
    ):
        raise ValueError(
            f"{field_name} is invalid."
        )

    return normalized


def build_profile_media_document(
    *,
    user_id,
    kind,
    storage_backend,
    storage_key,
    mime_type,
    size_bytes,
    width,
    height,
    position,
    sha256,
    is_primary=False,
    duration_ms=None,
    thumbnail_storage_key=None,
    thumbnail_mime_type=None,
    thumbnail_width=None,
    thumbnail_height=None,
    now=None,
):
    """
    Build canonical MongoDB metadata for a profile photo or video.

    Binary media is never stored in this document.
    """

    user_object_id = _to_object_id(
        user_id,
        field_name="user_id",
    )

    media_kind = normalize_media_kind(
        kind
    )

    backend = normalize_storage_backend(
        storage_backend
    )

    normalized_storage_key = (
        _normalize_storage_key(
            storage_key,
            field_name="storage_key",
        )
    )

    normalized_position = _positive_int(
        position,
        field_name="position",
        allow_zero=True,
    )

    normalized_size = _positive_int(
        size_bytes,
        field_name="size_bytes",
    )

    normalized_width = _positive_int(
        width,
        field_name="width",
    )

    normalized_height = _positive_int(
        height,
        field_name="height",
    )

    normalized_hash = _normalize_sha256(
        sha256
    )

    if not isinstance(
        mime_type,
        str,
    ) or not mime_type.strip():
        raise ValueError(
            "mime_type is required."
        )

    normalized_mime = (
        mime_type.strip().lower()
    )

    if media_kind == MEDIA_KIND_PROFILE_PHOTO:
        if normalized_mime != PHOTO_MIME_TYPE:
            raise ValueError(
                "Profile photos must use image/webp."
            )

        if duration_ms is not None:
            raise ValueError(
                "Profile photos cannot have duration_ms."
            )

        if thumbnail_storage_key is not None:
            raise ValueError(
                "Profile photos do not require a video thumbnail."
            )

        if thumbnail_mime_type is not None:
            raise ValueError(
                "Profile photos do not require thumbnail_mime_type."
            )

        if thumbnail_width is not None:
            raise ValueError(
                "Profile photos do not require thumbnail_width."
            )

        if thumbnail_height is not None:
            raise ValueError(
                "Profile photos do not require thumbnail_height."
            )

        normalized_duration = None
        normalized_thumbnail_key = None
        normalized_thumbnail_mime = None
        normalized_thumbnail_width = None
        normalized_thumbnail_height = None

    else:
        if normalized_mime != VIDEO_MIME_TYPE:
            raise ValueError(
                "Profile videos must use video/mp4."
            )

        normalized_duration = _positive_int(
            duration_ms,
            field_name="duration_ms",
        )

        normalized_thumbnail_key = (
            _normalize_storage_key(
                thumbnail_storage_key,
                field_name="thumbnail_storage_key",
            )
        )

        normalized_thumbnail_mime = (
            str(
                thumbnail_mime_type
            )
            .strip()
            .lower()
        )

        if (
            normalized_thumbnail_mime
            != VIDEO_THUMBNAIL_MIME_TYPE
        ):
            raise ValueError(
                "Video thumbnails must use image/webp."
            )

        normalized_thumbnail_width = (
            _positive_int(
                thumbnail_width,
                field_name="thumbnail_width",
            )
        )

        normalized_thumbnail_height = (
            _positive_int(
                thumbnail_height,
                field_name="thumbnail_height",
            )
        )

    timestamp = (
        now
        if isinstance(
            now,
            datetime,
        )
        else utc_now()
    )

    return {
        "user_id": user_object_id,
        "kind": media_kind,
        "storage_backend": backend,
        "storage_key": normalized_storage_key,
        "mime_type": normalized_mime,
        "size_bytes": normalized_size,
        "width": normalized_width,
        "height": normalized_height,
        "duration_ms": normalized_duration,
        "thumbnail_storage_key": (
            normalized_thumbnail_key
        ),
        "thumbnail_mime_type": (
            normalized_thumbnail_mime
        ),
        "thumbnail_width": (
            normalized_thumbnail_width
        ),
        "thumbnail_height": (
            normalized_thumbnail_height
        ),
        "position": normalized_position,
        "is_primary": bool(
            is_primary
        ),
        "status": MEDIA_STATUS_ACTIVE,
        "sha256": normalized_hash,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_profile_media_reference(
    media,
):
    """
    Build the lightweight reference stored inside profile.media.

    Storage details intentionally remain in profile_media only.
    """

    if not isinstance(
        media,
        dict,
    ):
        raise ValueError(
            "Media document is required."
        )

    media_id = _to_object_id(
        media.get(
            "_id"
        )
    )

    kind = normalize_media_kind(
        media.get(
            "kind"
        )
    )

    position = _positive_int(
        media.get(
            "position"
        ),
        field_name="position",
        allow_zero=True,
    )

    reference = {
        "media_id": str(
            media_id
        ),
        "kind": kind,
        "position": position,
    }

    duration_ms = media.get(
        "duration_ms"
    )

    if (
        kind
        == MEDIA_KIND_PROFILE_VIDEO
        and duration_ms is not None
    ):
        reference[
            "duration_ms"
        ] = _positive_int(
            duration_ms,
            field_name="duration_ms",
        )

    return reference


def _json_safe_value(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return str(
            value
        )

    if isinstance(
        value,
        datetime,
    ):
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )
        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    if isinstance(
        value,
        dict,
    ):
        return {
            key: _json_safe_value(
                item
            )
            for key, item in value.items()
        }

    if isinstance(
        value,
        (list, tuple),
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    return value


def serialize_profile_media(
    media,
):
    """
    Serialize active profile media without exposing storage internals.

    URLs are API-controlled paths rather than raw filesystem/object keys.
    """

    if not isinstance(
        media,
        dict,
    ):
        raise ValueError(
            "Media document is required."
        )

    media_id = _to_object_id(
        media.get(
            "_id"
        )
    )

    kind = normalize_media_kind(
        media.get(
            "kind"
        )
    )

    status = normalize_media_status(
        media.get(
            "status",
            MEDIA_STATUS_ACTIVE,
        )
    )

    result = {
        "id": str(
            media_id
        ),
        "kind": kind,
        "position": _positive_int(
            media.get(
                "position"
            ),
            field_name="position",
            allow_zero=True,
        ),
        "is_primary": bool(
            media.get(
                "is_primary",
                False,
            )
        ),
        "status": status,
        "mime_type": str(
            media.get(
                "mime_type",
                ""
            )
        ),
        "width": media.get(
            "width"
        ),
        "height": media.get(
            "height"
        ),
        "content_url": (
            f"/api/v1/media/{media_id}/content"
        ),
        "created_at": _json_safe_value(
            media.get(
                "created_at"
            )
        ),
        "updated_at": _json_safe_value(
            media.get(
                "updated_at"
            )
        ),
    }

    if (
        kind
        == MEDIA_KIND_PROFILE_VIDEO
    ):
        result[
            "duration_ms"
        ] = media.get(
            "duration_ms"
        )

        result[
            "thumbnail_url"
        ] = (
            f"/api/v1/media/{media_id}/thumbnail"
        )

    else:
        result[
            "duration_ms"
        ] = None

        result[
            "thumbnail_url"
        ] = None

    return result


__all__ = [
    "MEDIA_KIND_PROFILE_PHOTO",
    "MEDIA_KIND_PROFILE_VIDEO",
    "MEDIA_STATUS_ACTIVE",
    "MEDIA_STATUS_DELETED",
    "MEDIA_STATUSES",
    "MEDIA_STORAGE_LOCAL",
    "PHOTO_MIME_TYPE",
    "PROFILE_MEDIA_KINDS",
    "SUPPORTED_MEDIA_STORAGE_BACKENDS",
    "VIDEO_MIME_TYPE",
    "VIDEO_THUMBNAIL_MIME_TYPE",
    "build_profile_media_document",
    "build_profile_media_reference",
    "normalize_media_kind",
    "normalize_media_status",
    "normalize_storage_backend",
    "serialize_profile_media",
    "utc_now",
]
