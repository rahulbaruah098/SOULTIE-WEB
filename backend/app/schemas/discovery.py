"""Canonical discovery data contracts for Soultie.

Discovery operates only on information explicitly supplied by users.
It must never infer ethnicity, tribe, community, religion, gender, or
other sensitive traits from names, surnames, photos, location, or behavior.
"""

from datetime import datetime, timezone


DISCOVERY_DEFAULT_LIMIT = 20
DISCOVERY_MAX_LIMIT = 50
DISCOVERY_MAX_CURSOR_LENGTH = 512

DISCOVERY_ELIGIBLE_PROFILE_STATUSES = {
    "active",
}

DISCOVERY_ELIGIBLE_VISIBILITIES = {
    "visible",
}


# Internal profile fields that the discovery engine may need when deciding
# eligibility. Presence here does NOT mean a field may be returned publicly.
DISCOVERY_PROFILE_SOURCE_FIELDS = (
    "_id",
    "user_id",
    "profile_status",
    "visibility",
    "onboarding_status",
    "profile_completion_percent",
    "display_name",
    "first_name",
    "last_name",
    "surname_normalized",
    "surname_searchable",
    "birth_date",
    "gender_identity",
    "gender_custom_label",
    "pronouns",
    "current_city",
    "hometown",
    "location",
    "culture",
    "languages",
    "bio",
    "interests",
    "prompts",
    "height_cm",
    "education",
    "work",
    "lifestyle",
    "relationship_intentions",
    "media",
    "primary_media_id",
    "privacy",
    "verification_summary",
    "last_active_at",
    "created_at",
    "updated_at",
)


# Fields allowed in a discovery candidate returned to another user.
# Exact birth date, surname_normalized, precise location and internal
# lifecycle data are intentionally excluded.
DISCOVERY_PUBLIC_CANDIDATE_FIELDS = (
    "user_id",
    "display_name",
    "age",
    "gender_identity",
    "gender_custom_label",
    "pronouns",
    "current_city",
    "hometown",
    "culture",
    "languages",
    "bio",
    "interests",
    "prompts",
    "height_cm",
    "education",
    "work",
    "lifestyle",
    "relationship_intentions",
    "media",
    "primary_media_id",
    "verification_summary",
    "distance_km",
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def build_discovery_candidate(
    *,
    user_id,
    display_name,
    age,
    gender_identity=None,
    gender_custom_label=None,
    pronouns=None,
    current_city=None,
    hometown=None,
    culture=None,
    languages=None,
    bio=None,
    interests=None,
    prompts=None,
    height_cm=None,
    education=None,
    work=None,
    lifestyle=None,
    relationship_intentions=None,
    media=None,
    primary_media_id=None,
    verification_summary=None,
    distance_km=None,
):
    """Build the privacy-safe public discovery candidate contract."""

    return {
        "user_id": user_id,
        "display_name": display_name,
        "age": age,
        "gender_identity": gender_identity,
        "gender_custom_label": (
            gender_custom_label
        ),
        "pronouns": list(
            pronouns
            or []
        ),
        "current_city": dict(
            current_city
            or {}
        ),
        "hometown": dict(
            hometown
            or {}
        ),
        "culture": dict(
            culture
            or {}
        ),
        "languages": list(
            languages
            or []
        ),
        "bio": bio,
        "interests": list(
            interests
            or []
        ),
        "prompts": list(
            prompts
            or []
        ),
        "height_cm": height_cm,
        "education": dict(
            education
            or {}
        ),
        "work": dict(
            work
            or {}
        ),
        "lifestyle": dict(
            lifestyle
            or {}
        ),
        "relationship_intentions": list(
            relationship_intentions
            or []
        ),
        "media": list(
            media
            or []
        ),
        "primary_media_id": primary_media_id,
        "verification_summary": dict(
            verification_summary
            or {}
        ),
        "distance_km": distance_km,
    }


def build_discovery_page(
    *,
    candidates,
    limit,
    next_cursor=None,
):
    """Build the canonical paginated discovery response payload."""

    candidates = list(
        candidates
        or []
    )

    return {
        "candidates": candidates,
        "pagination": {
            "limit": limit,
            "returned": len(
                candidates
            ),
            "next_cursor": next_cursor,
            "has_more": (
                next_cursor
                is not None
            ),
        },
    }


__all__ = [
    "DISCOVERY_DEFAULT_LIMIT",
    "DISCOVERY_ELIGIBLE_PROFILE_STATUSES",
    "DISCOVERY_ELIGIBLE_VISIBILITIES",
    "DISCOVERY_MAX_CURSOR_LENGTH",
    "DISCOVERY_MAX_LIMIT",
    "DISCOVERY_PROFILE_SOURCE_FIELDS",
    "DISCOVERY_PUBLIC_CANDIDATE_FIELDS",
    "build_discovery_candidate",
    "build_discovery_page",
    "utc_now",
]
