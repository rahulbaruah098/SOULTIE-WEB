"""Canonical privacy-safe compatibility contracts for Xuoroni."""

from copy import deepcopy


COMPATIBILITY_ALGORITHM_VERSION = "v1"

COMPATIBILITY_SCORE_MIN = 0
COMPATIBILITY_SCORE_MAX = 100

COMPATIBILITY_DIMENSIONS = (
    "relationship_intentions",
    "interests",
    "languages",
    "culture",
    "festivals",
    "lifestyle",
    "distance",
)

COMPATIBILITY_BANDS = (
    (85, "very_strong"),
    (70, "strong"),
    (55, "good"),
    (40, "some"),
    (0, "limited"),
)


def _percentage(
    value,
    *,
    field,
    allow_none=False,
):
    if value is None:
        if allow_none:
            return None

        raise ValueError(
            f"{field} is required."
        )

    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise ValueError(
            f"{field} must be a number."
        )

    numeric = float(value)

    if not (
        COMPATIBILITY_SCORE_MIN
        <= numeric
        <= COMPATIBILITY_SCORE_MAX
    ):
        raise ValueError(
            (
                f"{field} must be between "
                f"{COMPATIBILITY_SCORE_MIN} and "
                f"{COMPATIBILITY_SCORE_MAX}."
            )
        )

    return int(
        round(numeric)
    )


def _non_negative_integer(
    value,
    *,
    field,
):
    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            int,
        )
        or value < 0
    ):
        raise ValueError(
            (
                f"{field} must be a "
                "non-negative integer."
            )
        )

    return value


def _string_list(
    values,
    *,
    field,
):
    if values is None:
        return []

    if not isinstance(
        values,
        (list, tuple),
    ):
        raise ValueError(
            f"{field} must be a list."
        )

    result = []
    seen = set()

    for value in values:
        if not isinstance(
            value,
            str,
        ):
            raise ValueError(
                (
                    f"{field} values "
                    "must be strings."
                )
            )

        cleaned = value.strip()

        if not cleaned:
            continue

        marker = cleaned.casefold()

        if marker in seen:
            continue

        seen.add(marker)
        result.append(cleaned)

    return result


def compatibility_band(
    score,
):
    normalized = _percentage(
        score,
        field="score",
        allow_none=True,
    )

    if normalized is None:
        return "insufficient_data"

    for minimum, band in (
        COMPATIBILITY_BANDS
    ):
        if normalized >= minimum:
            return band

    return "limited"


def build_compatibility_dimension(
    *,
    key,
    score=None,
    weight,
    available,
    evidence_count=0,
):
    if key not in COMPATIBILITY_DIMENSIONS:
        raise ValueError(
            "Unsupported compatibility dimension."
        )

    if not isinstance(
        available,
        bool,
    ):
        raise ValueError(
            "available must be true or false."
        )

    normalized_weight = _percentage(
        weight,
        field="weight",
    )

    normalized_evidence_count = (
        _non_negative_integer(
            evidence_count,
            field="evidence_count",
        )
    )

    if available:
        normalized_score = _percentage(
            score,
            field="score",
        )

    else:
        if score is not None:
            raise ValueError(
                (
                    "Unavailable compatibility "
                    "dimensions cannot contain "
                    "a score."
                )
            )

        normalized_score = None

    return {
        "key": key,
        "available": available,
        "score": normalized_score,
        "weight": normalized_weight,
        "evidence_count": (
            normalized_evidence_count
        ),
    }


def build_compatibility_reason(
    *,
    code,
    message,
    dimension=None,
):
    if not isinstance(
        code,
        str,
    ) or not code.strip():
        raise ValueError(
            "Compatibility reason code is required."
        )

    if not isinstance(
        message,
        str,
    ) or not message.strip():
        raise ValueError(
            (
                "Compatibility reason message "
                "is required."
            )
        )

    if (
        dimension is not None
        and dimension
        not in COMPATIBILITY_DIMENSIONS
    ):
        raise ValueError(
            (
                "Unsupported compatibility "
                "reason dimension."
            )
        )

    return {
        "code": code.strip(),
        "dimension": dimension,
        "message": message.strip(),
    }


def build_shared_compatibility(
    *,
    interests=None,
    languages=None,
    communities=None,
    tribes=None,
    heritage_tags=None,
    custom_entries=None,
    festivals=None,
    relationship_intentions=None,
    lifestyle=None,
):
    if (
        lifestyle is not None
        and not isinstance(
            lifestyle,
            dict,
        )
    ):
        raise ValueError(
            "lifestyle must be an object."
        )

    return {
        "interests": _string_list(
            interests,
            field="interests",
        ),
        "languages": _string_list(
            languages,
            field="languages",
        ),
        "culture": {
            "communities": _string_list(
                communities,
                field="communities",
            ),
            "tribes": _string_list(
                tribes,
                field="tribes",
            ),
            "heritage_tags": _string_list(
                heritage_tags,
                field="heritage_tags",
            ),
            "custom_entries": _string_list(
                custom_entries,
                field="custom_entries",
            ),
        },
        "festivals": _string_list(
            festivals,
            field="festivals",
        ),
        "relationship_intentions": (
            _string_list(
                relationship_intentions,
                field=(
                    "relationship_intentions"
                ),
            )
        ),
        "lifestyle": deepcopy(
            lifestyle
            or {}
        ),
    }


def build_compatibility_result(
    *,
    target_user_id,
    score,
    data_coverage_percent,
    dimensions=None,
    reasons=None,
    shared=None,
):
    target = str(
        target_user_id
        or ""
    ).strip()

    if not target:
        raise ValueError(
            "target_user_id is required."
        )

    normalized_score = _percentage(
        score,
        field="score",
        allow_none=True,
    )

    normalized_coverage = _percentage(
        data_coverage_percent,
        field="data_coverage_percent",
    )

    if dimensions is None:
        dimensions = []

    if not isinstance(
        dimensions,
        (list, tuple),
    ):
        raise ValueError(
            "dimensions must be a list."
        )

    if reasons is None:
        reasons = []

    if not isinstance(
        reasons,
        (list, tuple),
    ):
        raise ValueError(
            "reasons must be a list."
        )

    if shared is None:
        shared = (
            build_shared_compatibility()
        )

    if not isinstance(
        shared,
        dict,
    ):
        raise ValueError(
            "shared must be an object."
        )

    return {
        "algorithm_version": (
            COMPATIBILITY_ALGORITHM_VERSION
        ),
        "target_user_id": target,
        "score": normalized_score,
        "band": compatibility_band(
            normalized_score
        ),
        "has_sufficient_data": (
            normalized_score is not None
        ),
        "data_coverage_percent": (
            normalized_coverage
        ),
        "dimensions": deepcopy(
            list(dimensions)
        ),
        "reasons": deepcopy(
            list(reasons)
        ),
        "shared": deepcopy(
            shared
        ),
    }


__all__ = [
    "COMPATIBILITY_ALGORITHM_VERSION",
    "COMPATIBILITY_BANDS",
    "COMPATIBILITY_DIMENSIONS",
    "COMPATIBILITY_SCORE_MAX",
    "COMPATIBILITY_SCORE_MIN",
    "build_compatibility_dimension",
    "build_compatibility_reason",
    "build_compatibility_result",
    "build_shared_compatibility",
    "compatibility_band",
]