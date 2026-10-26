"""Deterministic privacy-safe compatibility scoring for Soultie."""

from math import asin, cos, radians, sin, sqrt

from bson import ObjectId
from bson.errors import InvalidId

from app.repositories.match_repository import (
    get_match_between,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
from app.repositories.safety_repository import (
    pair_is_blocked,
)
from app.repositories.user_repository import (
    get_user_by_id,
)

from app.schemas.compatibility import (
    COMPATIBILITY_DIMENSIONS,
    build_compatibility_dimension,
    build_compatibility_reason,
    build_compatibility_result,
    build_shared_compatibility,
)


EARTH_RADIUS_KM = 6371.0088

COMPATIBILITY_MIN_DATA_COVERAGE_PERCENT = 35

COMPATIBILITY_DIMENSION_WEIGHTS = {
    "relationship_intentions": 25,
    "interests": 20,
    "languages": 15,
    "culture": 10,
    "festivals": 10,
    "lifestyle": 15,
    "distance": 5,
}

CULTURE_FIELDS = (
    "communities",
    "tribes",
    "heritage_tags",
    "custom_entries",
)

LIFESTYLE_FIELDS = (
    "smoking",
    "drinking",
    "diet",
    "fitness",
    "pets",
)


if (
    set(COMPATIBILITY_DIMENSION_WEIGHTS)
    != set(COMPATIBILITY_DIMENSIONS)
):
    raise RuntimeError(
        "Compatibility weights do not match canonical dimensions."
    )


if sum(
    COMPATIBILITY_DIMENSION_WEIGHTS.values()
) != 100:
    raise RuntimeError(
        "Compatibility dimension weights must total 100."
    )


class CompatibilityServiceError(
    RuntimeError
):
    """Base compatibility-domain error."""

    def __init__(
        self,
        code,
        message,
        *,
        status_code=400,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message
        self.status_code = status_code


class CompatibilityValidationError(
    CompatibilityServiceError
):
    pass


class CompatibilityLifecycleError(
    CompatibilityServiceError
):
    pass


class CompatibilityNotFoundError(
    CompatibilityServiceError
):
    pass


def _to_object_id(
    value,
    *,
    field_name,
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
        raise CompatibilityValidationError(
            "INVALID_USER_ID",
            f"{field_name} is invalid.",
            status_code=400,
        ) from None


def _require_requester_user(
    user_id,
):
    user = get_user_by_id(
        user_id
    )

    if user is None:
        raise CompatibilityNotFoundError(
            "CURRENT_USER_NOT_FOUND",
            "Current user was not found.",
            status_code=404,
        )

    if (
        user.get(
            "account_status"
        )
        != "active"
    ):
        raise CompatibilityLifecycleError(
            "CURRENT_ACCOUNT_INACTIVE",
            (
                "Current account is not "
                "active."
            ),
            status_code=403,
        )

    return user


def _require_target_user(
    user_id,
):
    user = get_user_by_id(
        user_id
    )

    if (
        user is None
        or user.get(
            "account_status"
        )
        != "active"
    ):
        raise CompatibilityNotFoundError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    return user


def _require_requester_profile(
    user_id,
):
    profile = get_profile_by_user_id(
        user_id
    )

    if profile is None:
        raise CompatibilityNotFoundError(
            "CURRENT_PROFILE_NOT_FOUND",
            (
                "Current profile was "
                "not found."
            ),
            status_code=404,
        )

    if (
        profile.get(
            "onboarding_status"
        )
        != "completed"
    ):
        raise CompatibilityLifecycleError(
            "ONBOARDING_INCOMPLETE",
            (
                "Complete onboarding before "
                "using compatibility."
            ),
            status_code=409,
        )

    if (
        profile.get(
            "profile_status"
        )
        != "active"
    ):
        raise CompatibilityLifecycleError(
            "CURRENT_PROFILE_INACTIVE",
            (
                "Current profile is not "
                "active."
            ),
            status_code=403,
        )

    return profile


def _require_target_profile(
    user_id,
):
    profile = get_profile_by_user_id(
        user_id
    )

    if (
        profile is None
        or profile.get(
            "onboarding_status"
        )
        != "completed"
        or profile.get(
            "profile_status"
        )
        != "active"
    ):
        raise CompatibilityNotFoundError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    return profile


def _has_active_match(
    first_user_id,
    second_user_id,
):
    match = get_match_between(
        first_user_id,
        second_user_id,
    )

    return (
        match is not None
        and match.get(
            "status"
        )
        == "active"
    )


def _clean_string(value):
    if not isinstance(value, str):
        return None

    value = " ".join(
        value.strip().split()
    )

    return value or None


def _string_map(values):
    if not isinstance(
        values,
        (list, tuple),
    ):
        return {}

    result = {}

    for value in values:
        cleaned = _clean_string(
            value
        )

        if cleaned is None:
            continue

        marker = cleaned.casefold()

        if marker not in result:
            result[marker] = cleaned

    return result


def _overlap_dimension(
    first_values,
    second_values,
):
    first = _string_map(
        first_values
    )

    second = _string_map(
        second_values
    )

    if not first or not second:
        return {
            "available": False,
            "score": None,
            "shared": [],
            "evidence_count": 0,
        }

    shared_markers = (
        set(first)
        & set(second)
    )

    denominator = min(
        len(first),
        len(second),
    )

    score = round(
        (
            len(shared_markers)
            / denominator
        )
        * 100
    )

    shared = [
        first[marker]
        for marker in sorted(
            shared_markers
        )
    ]

    return {
        "available": True,
        "score": score,
        "shared": shared,
        "evidence_count": len(
            shared
        ),
    }


def _privacy_flag(
    profile,
    field,
    default=True,
):
    privacy = profile.get(
        "privacy"
    )

    if not isinstance(
        privacy,
        dict,
    ):
        privacy = {}

    return bool(
        privacy.get(
            field,
            default,
        )
    )


def _culture_dimension(
    first_profile,
    second_profile,
):
    empty_shared = {
        field: []
        for field in CULTURE_FIELDS
    }

    if (
        not _privacy_flag(
            first_profile,
            "show_community",
        )
        or not _privacy_flag(
            second_profile,
            "show_community",
        )
    ):
        return {
            "available": False,
            "score": None,
            "shared": empty_shared,
            "evidence_count": 0,
        }

    first_culture = (
        first_profile.get(
            "culture"
        )
        or {}
    )

    second_culture = (
        second_profile.get(
            "culture"
        )
        or {}
    )

    if not isinstance(
        first_culture,
        dict,
    ):
        first_culture = {}

    if not isinstance(
        second_culture,
        dict,
    ):
        second_culture = {}

    first = {
        field: _string_map(
            first_culture.get(
                field
            )
        )
        for field in CULTURE_FIELDS
    }

    second = {
        field: _string_map(
            second_culture.get(
                field
            )
        )
        for field in CULTURE_FIELDS
    }

    first_markers = set()
    second_markers = set()

    for field in CULTURE_FIELDS:
        first_markers.update(
            (
                field,
                marker,
            )
            for marker in first[field]
        )

        second_markers.update(
            (
                field,
                marker,
            )
            for marker in second[field]
        )

    if (
        not first_markers
        or not second_markers
    ):
        return {
            "available": False,
            "score": None,
            "shared": empty_shared,
            "evidence_count": 0,
        }

    shared_markers = (
        first_markers
        & second_markers
    )

    denominator = min(
        len(first_markers),
        len(second_markers),
    )

    score = round(
        (
            len(shared_markers)
            / denominator
        )
        * 100
    )

    shared = {
        field: []
        for field in CULTURE_FIELDS
    }

    for field, marker in sorted(
        shared_markers
    ):
        shared[field].append(
            first[field][marker]
        )

    return {
        "available": True,
        "score": score,
        "shared": shared,
        "evidence_count": len(
            shared_markers
        ),
    }


def _lifestyle_dimension(
    first_profile,
    second_profile,
):
    first = (
        first_profile.get(
            "lifestyle"
        )
        or {}
    )

    second = (
        second_profile.get(
            "lifestyle"
        )
        or {}
    )

    if not isinstance(first, dict):
        first = {}

    if not isinstance(second, dict):
        second = {}

    comparable = 0
    shared = {}

    for field in LIFESTYLE_FIELDS:
        first_value = _clean_string(
            first.get(field)
        )

        second_value = _clean_string(
            second.get(field)
        )

        if (
            first_value is None
            or second_value is None
        ):
            continue

        comparable += 1

        if (
            first_value.casefold()
            == second_value.casefold()
        ):
            shared[field] = first_value

    if comparable == 0:
        return {
            "available": False,
            "score": None,
            "shared": {},
            "evidence_count": 0,
        }

    return {
        "available": True,
        "score": round(
            (
                len(shared)
                / comparable
            )
            * 100
        ),
        "shared": shared,
        "evidence_count": len(
            shared
        ),
    }


def _normalize_point(value):
    if not isinstance(
        value,
        dict,
    ):
        return None

    if value.get("type") != "Point":
        return None

    coordinates = value.get(
        "coordinates"
    )

    if (
        not isinstance(
            coordinates,
            (list, tuple),
        )
        or len(coordinates) != 2
    ):
        return None

    longitude = coordinates[0]
    latitude = coordinates[1]

    if (
        isinstance(longitude, bool)
        or isinstance(latitude, bool)
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

    longitude = float(longitude)
    latitude = float(latitude)

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
            delta_latitude / 2
        )
        ** 2
        + cos(first_latitude)
        * cos(second_latitude)
        * sin(
            delta_longitude / 2
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

    return (
        2
        * EARTH_RADIUS_KM
        * asin(
            sqrt(value)
        )
    )


def _distance_score(
    distance_km,
):
    if distance_km <= 5:
        return 100

    if distance_km <= 10:
        return 90

    if distance_km <= 25:
        return 80

    if distance_km <= 50:
        return 65

    if distance_km <= 100:
        return 50

    if distance_km <= 250:
        return 35

    if distance_km <= 500:
        return 20

    return 10


def _distance_dimension(
    first_profile,
    second_profile,
):
    if (
        not _privacy_flag(
            first_profile,
            "show_distance",
        )
        or not _privacy_flag(
            second_profile,
            "show_distance",
        )
    ):
        return {
            "available": False,
            "score": None,
            "evidence_count": 0,
        }

    distance = _distance_km(
        first_profile.get(
            "location"
        ),
        second_profile.get(
            "location"
        ),
    )

    if distance is None:
        return {
            "available": False,
            "score": None,
            "evidence_count": 0,
        }

    return {
        "available": True,
        "score": _distance_score(
            distance
        ),
        "evidence_count": 1,
    }


def _profile_list(
    profile,
    field,
):
    value = profile.get(
        field
    )

    if isinstance(
        value,
        (list, tuple),
    ):
        return value

    return []


def calculate_profile_compatibility(
    first_profile,
    second_profile,
):
    """
    Calculate deterministic compatibility using only information
    explicitly supplied by both users.

    Missing data lowers coverage instead of being guessed or
    treated as incompatibility.
    """

    if not isinstance(
        first_profile,
        dict,
    ):
        raise ValueError(
            "first_profile must be an object."
        )

    if not isinstance(
        second_profile,
        dict,
    ):
        raise ValueError(
            "second_profile must be an object."
        )

    target_user_id = (
        second_profile.get(
            "user_id"
        )
    )

    if target_user_id is None:
        raise ValueError(
            "second_profile.user_id is required."
        )

    relationship = _overlap_dimension(
        _profile_list(
            first_profile,
            "relationship_intentions",
        ),
        _profile_list(
            second_profile,
            "relationship_intentions",
        ),
    )

    interests = _overlap_dimension(
        _profile_list(
            first_profile,
            "interests",
        ),
        _profile_list(
            second_profile,
            "interests",
        ),
    )

    languages = _overlap_dimension(
        _profile_list(
            first_profile,
            "languages",
        ),
        _profile_list(
            second_profile,
            "languages",
        ),
    )

    culture = _culture_dimension(
        first_profile,
        second_profile,
    )

    first_culture = (
        first_profile.get(
            "culture"
        )
        or {}
    )

    second_culture = (
        second_profile.get(
            "culture"
        )
        or {}
    )

    if not isinstance(
        first_culture,
        dict,
    ):
        first_culture = {}

    if not isinstance(
        second_culture,
        dict,
    ):
        second_culture = {}

    festivals = _overlap_dimension(
        first_culture.get(
            "festivals"
        ),
        second_culture.get(
            "festivals"
        ),
    )

    lifestyle = _lifestyle_dimension(
        first_profile,
        second_profile,
    )

    distance = _distance_dimension(
        first_profile,
        second_profile,
    )

    raw_dimensions = {
        "relationship_intentions": relationship,
        "interests": interests,
        "languages": languages,
        "culture": culture,
        "festivals": festivals,
        "lifestyle": lifestyle,
        "distance": distance,
    }

    dimensions = []
    weighted_total = 0
    available_weight = 0

    for key in COMPATIBILITY_DIMENSIONS:
        result = raw_dimensions[key]

        weight = (
            COMPATIBILITY_DIMENSION_WEIGHTS[
                key
            ]
        )

        dimensions.append(
            build_compatibility_dimension(
                key=key,
                score=result["score"],
                weight=weight,
                available=result[
                    "available"
                ],
                evidence_count=result[
                    "evidence_count"
                ],
            )
        )

        if result["available"]:
            available_weight += weight

            weighted_total += (
                result["score"]
                * weight
            )

    data_coverage_percent = (
        available_weight
    )

    if (
        data_coverage_percent
        < COMPATIBILITY_MIN_DATA_COVERAGE_PERCENT
    ):
        score = None

    else:
        score = round(
            weighted_total
            / available_weight
        )

    reasons = []

    if relationship["shared"]:
        reasons.append(
            build_compatibility_reason(
                code=(
                    "SHARED_RELATIONSHIP_INTENT"
                ),
                dimension=(
                    "relationship_intentions"
                ),
                message=(
                    "You are looking for a "
                    "similar kind of connection."
                ),
            )
        )

    if interests["shared"]:
        reasons.append(
            build_compatibility_reason(
                code="SHARED_INTERESTS",
                dimension="interests",
                message=(
                    "You share some interests."
                ),
            )
        )

    if languages["shared"]:
        reasons.append(
            build_compatibility_reason(
                code="SHARED_LANGUAGES",
                dimension="languages",
                message=(
                    "You share one or more languages."
                ),
            )
        )

    if (
        culture["evidence_count"]
        > 0
    ):
        reasons.append(
            build_compatibility_reason(
                code=(
                    "SHARED_CULTURAL_CONTEXT"
                ),
                dimension="culture",
                message=(
                    "You share some voluntarily "
                    "provided cultural context."
                ),
            )
        )

    if festivals["shared"]:
        reasons.append(
            build_compatibility_reason(
                code="SHARED_FESTIVALS",
                dimension="festivals",
                message=(
                    "You share festival interests."
                ),
            )
        )

    if (
        lifestyle["evidence_count"]
        > 0
    ):
        reasons.append(
            build_compatibility_reason(
                code="LIFESTYLE_ALIGNMENT",
                dimension="lifestyle",
                message=(
                    "Some of your lifestyle "
                    "preferences align."
                ),
            )
        )

    if (
        distance["available"]
        and distance["score"] >= 50
    ):
        reasons.append(
            build_compatibility_reason(
                code="PRACTICAL_DISTANCE",
                dimension="distance",
                message=(
                    "You are within a practical "
                    "distance of each other."
                ),
            )
        )

    if score is None:
        reasons.append(
            build_compatibility_reason(
                code=(
                    "MORE_PROFILE_DATA_NEEDED"
                ),
                message=(
                    "More profile information is "
                    "needed for a reliable "
                    "compatibility score."
                ),
            )
        )

    shared = build_shared_compatibility(
        interests=interests[
            "shared"
        ],
        languages=languages[
            "shared"
        ],
        communities=culture[
            "shared"
        ]["communities"],
        tribes=culture[
            "shared"
        ]["tribes"],
        heritage_tags=culture[
            "shared"
        ]["heritage_tags"],
        custom_entries=culture[
            "shared"
        ]["custom_entries"],
        festivals=festivals[
            "shared"
        ],
        relationship_intentions=(
            relationship["shared"]
        ),
        lifestyle=lifestyle[
            "shared"
        ],
    )

    return build_compatibility_result(
        target_user_id=target_user_id,
        score=score,
        data_coverage_percent=(
            data_coverage_percent
        ),
        dimensions=dimensions,
        reasons=reasons,
        shared=shared,
    )



def get_profile_compatibility(
    *,
    requester_id,
    target_user_id,
):
    """
    Return compatibility for an authorized pair.

    Visible active profiles are available normally.
    Hidden or paused visibility remains available only
    to an existing active match.

    Blocked and otherwise unavailable target profiles
    fail closed with one generic not-found response.
    """

    requester = _to_object_id(
        requester_id,
        field_name="requester_id",
    )

    target = _to_object_id(
        target_user_id,
        field_name="target_user_id",
    )

    if requester == target:
        raise CompatibilityValidationError(
            "SELF_COMPATIBILITY_NOT_ALLOWED",
            (
                "A profile cannot calculate "
                "compatibility with itself."
            ),
            status_code=400,
        )

    _require_requester_user(
        requester
    )

    _require_target_user(
        target
    )

    requester_profile = (
        _require_requester_profile(
            requester
        )
    )

    target_profile = (
        _require_target_profile(
            target
        )
    )

    if pair_is_blocked(
        requester,
        target,
    ):
        raise CompatibilityNotFoundError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    target_visible = (
        target_profile.get(
            "visibility"
        )
        == "visible"
    )

    if (
        not target_visible
        and not _has_active_match(
            requester,
            target,
        )
    ):
        raise CompatibilityNotFoundError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    return calculate_profile_compatibility(
        requester_profile,
        target_profile,
    )

__all__ = [
    "COMPATIBILITY_DIMENSION_WEIGHTS",
    "COMPATIBILITY_MIN_DATA_COVERAGE_PERCENT",
    "CompatibilityLifecycleError",
    "CompatibilityNotFoundError",
    "CompatibilityServiceError",
    "CompatibilityValidationError",
    "calculate_profile_compatibility",
    "get_profile_compatibility",
]
