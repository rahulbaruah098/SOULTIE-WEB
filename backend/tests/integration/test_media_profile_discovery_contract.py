"""Profile and Discovery integration contracts for Soultie media."""

import pytest

from app.services.discovery_service import (
    serialize_discovery_candidate,
)
from app.services.profile_validation import (
    ProfileValidationError,
    validate_profile_patch,
)


PHOTO_ID = (
    "507f1f77bcf86cd799439011"
)

VIDEO_ID = (
    "507f1f77bcf86cd799439012"
)


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "media",
            [],
        ),
        (
            "primary_media_id",
            PHOTO_ID,
        ),
    ],
)
def test_public_profile_patch_rejects_media_manipulation(
    field,
    value,
):
    with pytest.raises(
        ProfileValidationError
    ):
        validate_profile_patch(
            {
                field: value,
            }
        )


def test_discovery_photo_reference_has_controlled_content_url():
    candidate = (
        serialize_discovery_candidate(
            {
                "user_id": (
                    "507f1f77bcf86cd799439021"
                ),
                "display_name": (
                    "Media Discovery"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "media": [
                    {
                        "media_id": PHOTO_ID,
                        "kind": (
                            "profile_photo"
                        ),
                        "position": 0,
                    }
                ],
                "primary_media_id": (
                    PHOTO_ID
                ),
                "privacy": {
                    "show_age": False,
                    "show_distance": False,
                },
            }
        )
    )

    assert (
        candidate[
            "primary_media_id"
        ]
        == PHOTO_ID
    )

    assert len(
        candidate[
            "media"
        ]
    ) == 1

    media = candidate[
        "media"
    ][0]

    assert media == {
        "media_id": PHOTO_ID,
        "kind": "profile_photo",
        "position": 0,
        "content_url": (
            f"/api/v1/media/"
            f"{PHOTO_ID}/content"
        ),
        "duration_ms": None,
        "thumbnail_url": None,
    }

    assert (
        "storage_key"
        not in media
    )

    assert (
        "storage_backend"
        not in media
    )


def test_discovery_video_reference_has_content_and_thumbnail_urls():
    candidate = (
        serialize_discovery_candidate(
            {
                "user_id": (
                    "507f1f77bcf86cd799439022"
                ),
                "display_name": (
                    "Video Discovery"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "media": [
                    {
                        "media_id": VIDEO_ID,
                        "kind": (
                            "profile_video"
                        ),
                        "position": 0,
                        "duration_ms": 3000,
                    }
                ],
                "primary_media_id": (
                    VIDEO_ID
                ),
                "privacy": {
                    "show_age": False,
                    "show_distance": False,
                },
            }
        )
    )

    media = candidate[
        "media"
    ][0]

    assert (
        media[
            "content_url"
        ]
        == (
            f"/api/v1/media/"
            f"{VIDEO_ID}/content"
        )
    )

    assert (
        media[
            "thumbnail_url"
        ]
        == (
            f"/api/v1/media/"
            f"{VIDEO_ID}/thumbnail"
        )
    )

    assert (
        media[
            "duration_ms"
        ]
        == 3000
    )


def test_discovery_drops_malformed_media_references():
    candidate = (
        serialize_discovery_candidate(
            {
                "user_id": (
                    "507f1f77bcf86cd799439023"
                ),
                "display_name": (
                    "Malformed Media"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "media": [
                    None,
                    {},
                    {
                        "media_id": "",
                        "kind": (
                            "profile_photo"
                        ),
                        "position": 0,
                    },
                    {
                        "media_id": PHOTO_ID,
                        "kind": "",
                        "position": 0,
                    },
                    {
                        "media_id": PHOTO_ID,
                        "kind": (
                            "profile_photo"
                        ),
                        "position": -1,
                    },
                ],
                "primary_media_id": None,
                "privacy": {
                    "show_age": False,
                    "show_distance": False,
                },
            }
        )
    )

    assert (
        candidate[
            "media"
        ]
        == []
    )
