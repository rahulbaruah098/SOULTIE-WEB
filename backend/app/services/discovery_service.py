"""Business logic for Soultie profile discovery."""

from copy import deepcopy
from datetime import (
    date,
    datetime,
)
from math import (
    asin,
    cos,
    radians,
    sin,
    sqrt,
)

from bson import ObjectId

from app.repositories.discovery_repository import (
    find_discovery_candidate_profiles,
)
from app.repositories.profile_repository import (
    ensure_discovery_preferences,
    ensure_profile,
    touch_profile_activity,
)
from app.schemas.discovery import (
    build_discovery_candidate,
    build_discovery_page,
)
from app.schemas.media import (
    MEDIA_KIND_PROFILE_VIDEO,
)
from app.services.discovery_validation import (
    encode_discovery_cursor,
    validate_discovery_query,
)
from app.services.profile_validation import (
    calculate_age,
)


EARTH_RADIUS_KM = 6371.0088


class DiscoveryLifecycleError(
    RuntimeError
):
    def __init__(
        self,
        code,
        message,
    ):
        self.code = code
        self.message = message

        super().__init__(
            message
        )


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
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        list,
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    if isinstance(
        value,
        tuple,
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    return value


def _normalize_point(
    value,
):
    if not isinstance(
        value,
        dict,
    ):
        return None

    if (
        value.get("type")
        != "Point"
    ):
        return None

    coordinates = value.get(
        "coordinates"
    )

    if (
        not isinstance(
            coordinates,
            (list, tuple),
        )
        or len(
            coordinates
        )
        != 2
    ):
        return None

    longitude = coordinates[0]
    latitude = coordinates[1]

    if (
        isinstance(
            longitude,
            bool,
        )
        or isinstance(
            latitude,
            bool,
        )
        or not isinstance(
            longitude,
            (int, float),
        )
        or not isinstance(
            latitude,
            (int, float),
        )
    ):
        return None

    longitude = float(
        longitude
    )

    latitude = float(
        latitude
    )

    if not (
        -180.0
        <= longitude
        <= 180.0
    ):
        return None

    if not (
        -90.0
        <= latitude
        <= 90.0
    ):
        return None

    return (
        longitude,
        latitude,
    )


def _distance_km(
    first_location,
    second_location,
):
    first = _normalize_point(
        first_location
    )

    second = _normalize_point(
        second_location
    )

    if (
        first is None
        or second is None
    ):
        return None

    first_longitude = radians(
        first[0]
    )

    first_latitude = radians(
        first[1]
    )

    second_longitude = radians(
        second[0]
    )

    second_latitude = radians(
        second[1]
    )

    delta_longitude = (
        second_longitude
        - first_longitude
    )

    delta_latitude = (
        second_latitude
        - first_latitude
    )

    value = (
        sin(
            delta_latitude
            / 2
        )
        ** 2
        + cos(
            first_latitude
        )
        * cos(
            second_latitude
        )
        * sin(
            delta_longitude
            / 2
        )
        ** 2
    )

    value = min(
        1.0,
        max(
            0.0,
            value,
        ),
    )

    distance = (
        2
        * EARTH_RADIUS_KM
        * asin(
            sqrt(
                value
            )
        )
    )

    return round(
        distance,
        1,
    )


def _privacy_safe_culture(
    profile,
):
    culture = deepcopy(
        profile.get(
            "culture"
        )
        or {}
    )

    privacy = (
        profile.get(
            "privacy"
        )
        or {}
    )

    if not privacy.get(
        "show_community",
        True,
    ):
        # Hide explicit community/tribe identity.
        # Festival interests may remain visible.
        culture[
            "communities"
        ] = []

        culture[
            "tribes"
        ] = []

        culture[
            "heritage_tags"
        ] = []

        culture[
            "custom_entries"
        ] = []

    return culture


def _privacy_safe_mapping(
    profile,
    field,
    privacy_field,
):
    privacy = (
        profile.get(
            "privacy"
        )
        or {}
    )

    if not privacy.get(
        privacy_field,
        True,
    ):
        return {}

    value = profile.get(
        field
    )

    if not isinstance(
        value,
        dict,
    ):
        return {}

    return deepcopy(
        value
    )


def _serialize_discovery_media(
    media,
):
    """
    Convert lightweight profile.media references into safe,
    API-renderable discovery media references.
    """

    if not isinstance(
        media,
        list,
    ):
        return []

    result = []

    for item in media:
        if not isinstance(
            item,
            dict,
        ):
            continue

        media_id = str(
            item.get(
                "media_id",
                "",
            )
        ).strip()

        kind = str(
            item.get(
                "kind",
                "",
            )
        ).strip()

        position = item.get(
            "position"
        )

        if (
            not media_id
            or not kind
            or not isinstance(
                position,
                int,
            )
            or isinstance(
                position,
                bool,
            )
            or position < 0
        ):
            continue

        public_item = {
            "media_id": media_id,
            "kind": kind,
            "position": position,
            "content_url": (
                f"/api/v1/media/"
                f"{media_id}/content"
            ),
        }

        if (
            kind
            == MEDIA_KIND_PROFILE_VIDEO
        ):
            public_item[
                "duration_ms"
            ] = item.get(
                "duration_ms"
            )

            public_item[
                "thumbnail_url"
            ] = (
                f"/api/v1/media/"
                f"{media_id}/thumbnail"
            )

        else:
            public_item[
                "duration_ms"
            ] = None

            public_item[
                "thumbnail_url"
            ] = None

        result.append(
            public_item
        )

    return result

def serialize_discovery_candidate(
    profile,
    *,
    requester_location=None,
    today=None,
):
    """Convert an internal profile into a privacy-safe discovery card."""

    if not isinstance(
        profile,
        dict,
    ):
        raise ValueError(
            "Discovery profile must be an object."
        )

    privacy = (
        profile.get(
            "privacy"
        )
        or {}
    )

    if privacy.get(
        "show_age",
        True,
    ):
        age = calculate_age(
            profile.get(
                "birth_date"
            ),
            today=today,
        )

    else:
        age = None

    if privacy.get(
        "show_distance",
        True,
    ):
        distance_km = _distance_km(
            requester_location,
            profile.get(
                "location"
            ),
        )

    else:
        distance_km = None

    hometown = (
        _privacy_safe_mapping(
            profile,
            "hometown",
            "show_hometown",
        )
    )

    work = (
        _privacy_safe_mapping(
            profile,
            "work",
            "show_work",
        )
    )

    education = (
        _privacy_safe_mapping(
            profile,
            "education",
            "show_education",
        )
    )

    candidate = (
        build_discovery_candidate(
            user_id=profile.get(
                "user_id"
            ),
            display_name=profile.get(
                "display_name"
            ),
            age=age,
            gender_identity=profile.get(
                "gender_identity"
            ),
            gender_custom_label=(
                profile.get(
                    "gender_custom_label"
                )
            ),
            pronouns=profile.get(
                "pronouns"
            ),
            current_city=profile.get(
                "current_city"
            ),
            hometown=hometown,
            culture=(
                _privacy_safe_culture(
                    profile
                )
            ),
            languages=profile.get(
                "languages"
            ),
            bio=profile.get(
                "bio"
            ),
            interests=profile.get(
                "interests"
            ),
            prompts=profile.get(
                "prompts"
            ),
            height_cm=profile.get(
                "height_cm"
            ),
            education=education,
            work=work,
            lifestyle=profile.get(
                "lifestyle"
            ),
            relationship_intentions=(
                profile.get(
                    "relationship_intentions"
                )
            ),
            media=(
                _serialize_discovery_media(
                    profile.get(
                        "media"
                    )
                )
            ),
            primary_media_id=(
                profile.get(
                    "primary_media_id"
                )
            ),
            verification_summary=(
                profile.get(
                    "verification_summary"
                )
            ),
            distance_km=distance_km,
        )
    )

    # Surname, birth date, precise location, internal profile ID,
    # lifecycle state and exact activity timestamps never enter
    # the public candidate contract.
    return _json_safe_value(
        candidate
    )


def _ensure_requester_can_discover(
    profile,
):
    if (
        profile.get(
            "profile_status"
        )
        == "disabled"
    ):
        raise DiscoveryLifecycleError(
            "DISCOVERY_PROFILE_DISABLED",
            (
                "This profile is currently "
                "disabled."
            ),
        )

    if (
        profile.get(
            "profile_status"
        )
        != "active"
        or profile.get(
            "onboarding_status"
        )
        != "completed"
    ):
        raise DiscoveryLifecycleError(
            "DISCOVERY_ONBOARDING_INCOMPLETE",
            (
                "Complete your profile and "
                "onboarding before using "
                "discovery."
            ),
        )


def get_discovery_page(
    user_id,
    *,
    limit=None,
    cursor=None,
    today=None,
):
    """Return one privacy-safe deterministic discovery page."""

    query_options = (
        validate_discovery_query(
            limit=limit,
            cursor=cursor,
        )
    )

    profile = ensure_profile(
        user_id
    )

    _ensure_requester_can_discover(
        profile
    )

    preferences = (
        ensure_discovery_preferences(
            user_id
        )
    )

    requester_location = (
        profile.get(
            "location"
        )
    )

    result = (
        find_discovery_candidate_profiles(
            requester_id=user_id,
            preferences=preferences,
            limit=query_options[
                "limit"
            ],
            cursor=query_options[
                "cursor"
            ],
            requester_location=(
                requester_location
            ),
            today=today,
        )
    )

    profiles = result.get(
        "profiles"
    ) or []

    candidates = [
        serialize_discovery_candidate(
            candidate_profile,
            requester_location=(
                requester_location
            ),
            today=today,
        )
        for candidate_profile
        in profiles
    ]

    next_cursor = None

    if (
        result.get(
            "has_more"
        )
        and profiles
    ):
        final_profile = profiles[
            -1
        ]

        last_active_at = (
            final_profile.get(
                "last_active_at"
            )
        )

        profile_id = (
            final_profile.get(
                "_id"
            )
        )

        if (
            last_active_at is None
            or profile_id is None
        ):
            raise RuntimeError(
                (
                    "Discovery cursor fields "
                    "are missing."
                )
            )

        next_cursor = (
            encode_discovery_cursor(
                last_active_at=(
                    last_active_at
                ),
                profile_id=(
                    profile_id
                ),
            )
        )

    touch_profile_activity(
        user_id
    )

    return build_discovery_page(
        candidates=candidates,
        limit=query_options[
            "limit"
        ],
        next_cursor=next_cursor,
    )


__all__ = [
    "DiscoveryLifecycleError",
    "get_discovery_page",
    "serialize_discovery_candidate",
]
