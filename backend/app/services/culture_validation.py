"""Validation and normalization rules for Soultie culture knowledge."""

import re
import unicodedata
from urllib.parse import (
    urlsplit,
    urlunsplit,
)

from app.schemas.culture import (
    CULTURE_ENTITY_TYPES,
    CULTURE_REVIEW_STATUSES,
    CULTURE_SOURCE_TYPES,
    FESTIVAL_DATE_TYPES,
)


class CultureValidationError(ValueError):
    def __init__(
        self,
        field: str,
        message: str,
    ):
        self.field = field
        self.message = message

        super().__init__(
            message
        )


def _fail(
    field: str,
    message: str,
):
    raise CultureValidationError(
        field,
        message,
    )


def _collapse_spaces(
    value: str,
) -> str:
    return " ".join(
        value.strip().split()
    )


def _optional_text(
    value,
    field: str,
    *,
    max_length: int,
):
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        _fail(
            field,
            "Must be text.",
        )

    value = _collapse_spaces(
        value
    )

    if not value:
        return None

    if len(value) > max_length:
        _fail(
            field,
            (
                f"Must be {max_length} "
                "characters or fewer."
            ),
        )

    for character in value:
        if unicodedata.category(
            character
        ) == "Cc":
            _fail(
                field,
                "Contains invalid characters.",
            )

    return value


def _required_text(
    value,
    field: str,
    *,
    max_length: int,
):
    value = _optional_text(
        value,
        field,
        max_length=max_length,
    )

    if value is None:
        _fail(
            field,
            "This field is required.",
        )

    return value


def _boolean(
    value,
    field: str,
):
    if not isinstance(
        value,
        bool,
    ):
        _fail(
            field,
            "Must be true or false.",
        )

    return value


def _string_list(
    value,
    field: str,
    *,
    max_items: int,
    item_max_length: int,
):
    if not isinstance(
        value,
        list,
    ):
        _fail(
            field,
            "Must be a list.",
        )

    if len(value) > max_items:
        _fail(
            field,
            (
                f"Cannot contain more than "
                f"{max_items} items."
            ),
        )

    result = []
    seen = set()

    for index, item in enumerate(
        value
    ):
        cleaned = _required_text(
            item,
            f"{field}[{index}]",
            max_length=item_max_length,
        )

        marker = normalize_search_text(
            cleaned
        )

        if marker in seen:
            continue

        seen.add(
            marker
        )

        result.append(
            cleaned
        )

    return result


def normalize_search_text(
    value,
):
    if value is None:
        return None

    cleaned = _optional_text(
        value,
        "value",
        max_length=500,
    )

    if cleaned is None:
        return None

    normalized = unicodedata.normalize(
        "NFKC",
        cleaned,
    ).casefold()

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()

    return normalized or None


def normalize_slug(
    value,
):
    cleaned = _required_text(
        value,
        "slug",
        max_length=120,
    )

    normalized = unicodedata.normalize(
        "NFKC",
        cleaned,
    ).casefold()

    normalized = re.sub(
        r"[\s_]+",
        "-",
        normalized,
    )

    normalized = re.sub(
        r"[^\w\-]+",
        "",
        normalized,
        flags=re.UNICODE,
    )

    normalized = re.sub(
        r"-{2,}",
        "-",
        normalized,
    ).strip("-")

    if not normalized:
        _fail(
            "slug",
            "Enter a valid slug.",
        )

    if len(normalized) > 120:
        _fail(
            "slug",
            "Slug is too long.",
        )

    return normalized


def normalize_source_url(
    value,
):
    url = _required_text(
        value,
        "source_url",
        max_length=2048,
    )

    try:
        parts = urlsplit(
            url
        )
    except ValueError:
        _fail(
            "source_url",
            "Enter a valid source URL.",
        )

    scheme = parts.scheme.casefold()

    if scheme not in {
        "http",
        "https",
    }:
        _fail(
            "source_url",
            (
                "Source URL must use "
                "http or https."
            ),
        )

    if not parts.hostname:
        _fail(
            "source_url",
            "Source URL must include a host.",
        )

    hostname = parts.hostname.casefold()

    try:
        port = parts.port
    except ValueError:
        _fail(
            "source_url",
            "Source URL contains an invalid port.",
        )

    if (
        port is not None
        and not (
            scheme == "http"
            and port == 80
        )
        and not (
            scheme == "https"
            and port == 443
        )
    ):
        host = (
            f"{hostname}:{port}"
        )
    else:
        host = hostname

    path = parts.path or "/"

    normalized = urlunsplit(
        (
            scheme,
            host,
            path,
            parts.query,
            "",
        )
    )

    return normalized


def validate_culture_source_payload(
    payload,
):
    if not isinstance(
        payload,
        dict,
    ):
        _fail(
            "source",
            "Source must be an object.",
        )

    allowed = {
        "source_url",
        "title",
        "publisher",
        "source_type",
        "published_at",
        "accessed_at",
    }

    unknown = (
        set(payload)
        - allowed
    )

    if unknown:
        _fail(
            "source",
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    source_url = _required_text(
        payload.get(
            "source_url"
        ),
        "source_url",
        max_length=2048,
    )

    source_type = payload.get(
        "source_type",
        "other",
    )

    if source_type not in CULTURE_SOURCE_TYPES:
        _fail(
            "source_type",
            "Unsupported culture source type.",
        )

    return {
        "source_url": source_url,
        "source_url_normalized": (
            normalize_source_url(
                source_url
            )
        ),
        "title": _required_text(
            payload.get(
                "title"
            ),
            "title",
            max_length=300,
        ),
        "publisher": _optional_text(
            payload.get(
                "publisher"
            ),
            "publisher",
            max_length=200,
        ),
        "source_type": source_type,
        "published_at": payload.get(
            "published_at"
        ),
        "accessed_at": payload.get(
            "accessed_at"
        ),
    }


def validate_culture_entity_payload(
    payload,
    *,
    partial=False,
):
    if not isinstance(
        payload,
        dict,
    ):
        _fail(
            "entity",
            "Culture entity must be an object.",
        )

    allowed = {
        "entity_type",
        "slug",
        "name",
        "aliases",
        "search_terms",
        "summary",
        "description",
        "regions",
        "languages",
        "related_entities",
        "source_ids",
        "provenance_notes",
        "review_status",
        "published",
    }

    unknown = (
        set(payload)
        - allowed
    )

    if unknown:
        _fail(
            "entity",
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    result = {}

    if (
        not partial
        or "entity_type" in payload
    ):
        entity_type = payload.get(
            "entity_type"
        )

        if entity_type not in CULTURE_ENTITY_TYPES:
            _fail(
                "entity_type",
                "Unsupported culture entity type.",
            )

        result[
            "entity_type"
        ] = entity_type

    if (
        not partial
        or "slug" in payload
    ):
        result[
            "slug"
        ] = normalize_slug(
            payload.get(
                "slug"
            )
        )

    if (
        not partial
        or "name" in payload
    ):
        name = _required_text(
            payload.get(
                "name"
            ),
            "name",
            max_length=160,
        )

        result[
            "name"
        ] = name

        result[
            "name_normalized"
        ] = normalize_search_text(
            name
        )

    list_fields = {
        "aliases": (30, 160),
        "search_terms": (40, 160),
        "regions": (30, 120),
        "languages": (30, 100),
        "related_entities": (50, 160),
        "source_ids": (50, 100),
    }

    for field, limits in (
        list_fields.items()
    ):
        if field not in payload:
            continue

        result[field] = _string_list(
            payload[field],
            field,
            max_items=limits[0],
            item_max_length=limits[1],
        )

    text_fields = {
        "summary": 500,
        "description": 5000,
        "provenance_notes": 2000,
    }

    for field, maximum in (
        text_fields.items()
    ):
        if field not in payload:
            continue

        result[field] = _optional_text(
            payload[field],
            field,
            max_length=maximum,
        )

    if "review_status" in payload:
        status = payload[
            "review_status"
        ]

        if status not in CULTURE_REVIEW_STATUSES:
            _fail(
                "review_status",
                "Unsupported review status.",
            )

        result[
            "review_status"
        ] = status

    if "published" in payload:
        result[
            "published"
        ] = _boolean(
            payload[
                "published"
            ],
            "published",
        )

    return result


def validate_festival_payload(
    payload,
    *,
    partial=False,
):
    if not isinstance(
        payload,
        dict,
    ):
        _fail(
            "festival",
            "Festival must be an object.",
        )

    allowed = {
        "slug",
        "name",
        "aliases",
        "search_terms",
        "summary",
        "description",
        "regions",
        "communities",
        "tribes",
        "languages",
        "timing",
        "related_entities",
        "source_ids",
        "provenance_notes",
        "review_status",
        "published",
    }

    unknown = (
        set(payload)
        - allowed
    )

    if unknown:
        _fail(
            "festival",
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    result = {}

    if (
        not partial
        or "slug" in payload
    ):
        result[
            "slug"
        ] = normalize_slug(
            payload.get(
                "slug"
            )
        )

    if (
        not partial
        or "name" in payload
    ):
        name = _required_text(
            payload.get(
                "name"
            ),
            "name",
            max_length=160,
        )

        result[
            "name"
        ] = name

        result[
            "name_normalized"
        ] = normalize_search_text(
            name
        )

    list_fields = {
        "aliases": (30, 160),
        "search_terms": (40, 160),
        "regions": (30, 120),
        "communities": (30, 120),
        "tribes": (30, 120),
        "languages": (30, 100),
        "related_entities": (50, 160),
        "source_ids": (50, 100),
    }

    for field, limits in (
        list_fields.items()
    ):
        if field not in payload:
            continue

        result[field] = _string_list(
            payload[field],
            field,
            max_items=limits[0],
            item_max_length=limits[1],
        )

    text_fields = {
        "summary": 500,
        "description": 5000,
        "provenance_notes": 2000,
    }

    for field, maximum in (
        text_fields.items()
    ):
        if field not in payload:
            continue

        result[field] = _optional_text(
            payload[field],
            field,
            max_length=maximum,
        )

    if "timing" in payload:
        timing = payload[
            "timing"
        ]

        if not isinstance(
            timing,
            dict,
        ):
            _fail(
                "timing",
                "Must be an object.",
            )

        allowed_timing = {
            "date_type",
            "month",
            "day",
            "date_text",
            "calendar_note",
        }

        unknown_timing = (
            set(timing)
            - allowed_timing
        )

        if unknown_timing:
            _fail(
                "timing",
                "Contains unsupported fields.",
            )

        timing_result = {}

        if "date_type" in timing:
            date_type = timing[
                "date_type"
            ]

            if date_type not in FESTIVAL_DATE_TYPES:
                _fail(
                    "timing.date_type",
                    "Unsupported festival date type.",
                )

            timing_result[
                "date_type"
            ] = date_type

        for field in (
            "month",
            "day",
        ):
            if field not in timing:
                continue

            value = timing[
                field
            ]

            if value is not None:
                if (
                    isinstance(
                        value,
                        bool,
                    )
                    or not isinstance(
                        value,
                        int,
                    )
                ):
                    _fail(
                        f"timing.{field}",
                        "Must be an integer or null.",
                    )

                maximum = (
                    12
                    if field == "month"
                    else 31
                )

                if (
                    value < 1
                    or value > maximum
                ):
                    _fail(
                        f"timing.{field}",
                        (
                            f"Must be between "
                            f"1 and {maximum}."
                        ),
                    )

            timing_result[
                field
            ] = value

        for field, maximum in (
            ("date_text", 200),
            ("calendar_note", 500),
        ):
            if field not in timing:
                continue

            timing_result[
                field
            ] = _optional_text(
                timing[
                    field
                ],
                f"timing.{field}",
                max_length=maximum,
            )

        result[
            "timing"
        ] = timing_result

    if "review_status" in payload:
        status = payload[
            "review_status"
        ]

        if status not in CULTURE_REVIEW_STATUSES:
            _fail(
                "review_status",
                "Unsupported review status.",
            )

        result[
            "review_status"
        ] = status

    if "published" in payload:
        result[
            "published"
        ] = _boolean(
            payload[
                "published"
            ],
            "published",
        )

    return result


__all__ = [
    "CultureValidationError",
    "normalize_search_text",
    "normalize_slug",
    "normalize_source_url",
    "validate_culture_entity_payload",
    "validate_culture_source_payload",
    "validate_festival_payload",
]
