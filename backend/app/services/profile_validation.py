"""Validation and normalization rules for Soultie profiles."""

import re
import unicodedata
from datetime import date, datetime, timezone

from app.schemas.profile import (
    GENDER_IDENTITIES,
    MINIMUM_USER_AGE,
    RELATIONSHIP_INTENTIONS,
)


MAXIMUM_USER_AGE = 120

SURNAME_FILTER_MODES = {
    "none",
    "include",
    "exclude",
}

DISCOVERY_GENDERS = (
    GENDER_IDENTITIES
    - {
        "prefer_not_to_say",
    }
)


class ProfileValidationError(ValueError):
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
    raise ProfileValidationError(
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
    min_length: int = 1,
):
    value = _optional_text(
        value,
        field,
        max_length=max_length,
    )

    if (
        value is None
        or len(value) < min_length
    ):
        _fail(
            field,
            (
                f"Must contain at least "
                f"{min_length} character(s)."
            ),
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


def _integer(
    value,
    field: str,
    *,
    minimum: int,
    maximum: int,
):
    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            int,
        )
    ):
        _fail(
            field,
            "Must be an integer.",
        )

    if (
        value < minimum
        or value > maximum
    ):
        _fail(
            field,
            (
                f"Must be between "
                f"{minimum} and {maximum}."
            ),
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

        marker = cleaned.casefold()

        if marker in seen:
            continue

        seen.add(
            marker
        )

        result.append(
            cleaned
        )

    return result


def normalize_surname(
    surname,
):
    if surname is None:
        return None

    cleaned = _optional_text(
        surname,
        "last_name",
        max_length=80,
    )

    if cleaned is None:
        return None

    normalized = (
        unicodedata.normalize(
            "NFKC",
            cleaned,
        )
        .casefold()
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    ).strip()

    return normalized or None


def _birth_date_from_value(
    value,
    field="birth_date",
):
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        parsed = value.date()

    elif isinstance(
        value,
        date,
    ):
        parsed = value

    elif isinstance(
        value,
        str,
    ):
        try:
            parsed = date.fromisoformat(
                value.strip()
            )

        except ValueError:
            _fail(
                field,
                (
                    "Must use YYYY-MM-DD "
                    "format."
                ),
            )

    else:
        _fail(
            field,
            "Must use YYYY-MM-DD format.",
        )

    return parsed


def calculate_age(
    birth_date,
    *,
    today=None,
):
    parsed = _birth_date_from_value(
        birth_date
    )

    if parsed is None:
        return None

    current_date = (
        today
        or datetime.now(
            timezone.utc
        ).date()
    )

    return (
        current_date.year
        - parsed.year
        - (
            (
                current_date.month,
                current_date.day,
            )
            < (
                parsed.month,
                parsed.day,
            )
        )
    )


def validate_birth_date(
    value,
    *,
    today=None,
):
    if value is None:
        return None

    parsed = _birth_date_from_value(
        value
    )

    current_date = (
        today
        or datetime.now(
            timezone.utc
        ).date()
    )

    if parsed > current_date:
        _fail(
            "birth_date",
            "Birth date cannot be in the future.",
        )

    age = calculate_age(
        parsed,
        today=current_date,
    )

    if age < MINIMUM_USER_AGE:
        _fail(
            "birth_date",
            (
                "Xuoroni is available only "
                "to users aged 18 or older."
            ),
        )

    if age > MAXIMUM_USER_AGE:
        _fail(
            "birth_date",
            "Enter a valid birth date.",
        )

    # Store as ISO YYYY-MM-DD.
    # This is Mongo-safe and naturally sortable.
    return parsed.isoformat()


def _text_mapping_updates(
    value,
    field: str,
    *,
    allowed,
):
    if not isinstance(
        value,
        dict,
    ):
        _fail(
            field,
            "Must be an object.",
        )

    unknown = (
        set(value)
        - set(allowed)
    )

    if unknown:
        _fail(
            field,
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    updates = {}

    for key, maximum in allowed.items():
        if key not in value:
            continue

        updates[
            f"{field}.{key}"
        ] = _optional_text(
            value[key],
            f"{field}.{key}",
            max_length=maximum,
        )

    return updates


def _validate_prompts(
    value,
):
    if not isinstance(
        value,
        list,
    ):
        _fail(
            "prompts",
            "Must be a list.",
        )

    if len(value) > 3:
        _fail(
            "prompts",
            (
                "A profile can contain "
                "up to 3 prompts."
            ),
        )

    result = []
    seen = set()

    for index, item in enumerate(
        value
    ):
        field = (
            f"prompts[{index}]"
        )

        if not isinstance(
            item,
            dict,
        ):
            _fail(
                field,
                "Must be an object.",
            )

        unknown = (
            set(item)
            - {
                "prompt_id",
                "answer",
            }
        )

        if unknown:
            _fail(
                field,
                "Contains unsupported fields.",
            )

        prompt_id = _required_text(
            item.get(
                "prompt_id"
            ),
            f"{field}.prompt_id",
            max_length=80,
        )

        answer = _required_text(
            item.get(
                "answer"
            ),
            f"{field}.answer",
            max_length=300,
        )

        marker = prompt_id.casefold()

        if marker in seen:
            _fail(
                field,
                "Duplicate profile prompt.",
            )

        seen.add(
            marker
        )

        result.append(
            {
                "prompt_id": prompt_id,
                "answer": answer,
            }
        )

    return result


def validate_profile_patch(
    patch,
    *,
    existing_profile=None,
    today=None,
):
    if not isinstance(
        patch,
        dict,
    ):
        _fail(
            "profile",
            "Profile update must be an object.",
        )

    if not patch:
        _fail(
            "profile",
            "No profile fields were provided.",
        )

    allowed_fields = {
        "display_name",
        "first_name",
        "last_name",
        "surname_searchable",
        "birth_date",
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
        "privacy",
    }

    unknown = (
        set(patch)
        - allowed_fields
    )

    if unknown:
        _fail(
            "profile",
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    existing_profile = (
        existing_profile
        or {}
    )

    updates = {}

    if "display_name" in patch:
        updates[
            "display_name"
        ] = _required_text(
            patch["display_name"],
            "display_name",
            max_length=50,
            min_length=2,
        )

    for field, maximum in (
        ("first_name", 50),
        ("last_name", 80),
        ("bio", 500),
    ):
        if field not in patch:
            continue

        updates[field] = (
            _optional_text(
                patch[field],
                field,
                max_length=maximum,
            )
        )

    if "last_name" in patch:
        updates[
            "surname_normalized"
        ] = normalize_surname(
            updates[
                "last_name"
            ]
        )

    if (
        "surname_searchable"
        in patch
    ):
        updates[
            "surname_searchable"
        ] = _boolean(
            patch[
                "surname_searchable"
            ],
            "surname_searchable",
        )

    if "birth_date" in patch:
        updates[
            "birth_date"
        ] = validate_birth_date(
            patch[
                "birth_date"
            ],
            today=today,
        )

    prospective_gender = (
        patch.get(
            "gender_identity",
            existing_profile.get(
                "gender_identity"
            ),
        )
    )

    if (
        "gender_identity"
        in patch
    ):
        if (
            prospective_gender
            is not None
            and prospective_gender
            not in GENDER_IDENTITIES
        ):
            _fail(
                "gender_identity",
                "Unsupported gender value.",
            )

        updates[
            "gender_identity"
        ] = prospective_gender

    if (
        "gender_custom_label"
        in patch
    ):
        if (
            prospective_gender
            != "self_described"
        ):
            _fail(
                "gender_custom_label",
                (
                    "A custom gender label is "
                    "only used with self_described."
                ),
            )

        updates[
            "gender_custom_label"
        ] = _required_text(
            patch[
                "gender_custom_label"
            ],
            "gender_custom_label",
            max_length=50,
        )

    if (
        "gender_identity"
        in patch
        and prospective_gender
        != "self_described"
    ):
        updates[
            "gender_custom_label"
        ] = None

    if (
        prospective_gender
        == "self_described"
    ):
        prospective_label = (
            updates.get(
                "gender_custom_label",
                existing_profile.get(
                    "gender_custom_label"
                ),
            )
        )

        if not prospective_label:
            _fail(
                "gender_custom_label",
                (
                    "Enter your self-described "
                    "gender label."
                ),
            )

    if "pronouns" in patch:
        updates["pronouns"] = (
            _string_list(
                patch["pronouns"],
                "pronouns",
                max_items=5,
                item_max_length=30,
            )
        )

    if "current_city" in patch:
        updates.update(
            _text_mapping_updates(
                patch["current_city"],
                "current_city",
                allowed={
                    "city": 100,
                    "state": 100,
                    "country": 80,
                },
            )
        )

    if "hometown" in patch:
        updates.update(
            _text_mapping_updates(
                patch["hometown"],
                "hometown",
                allowed={
                    "city": 100,
                    "state": 100,
                    "country": 80,
                },
            )
        )

    if "culture" in patch:
        culture = patch[
            "culture"
        ]

        if not isinstance(
            culture,
            dict,
        ):
            _fail(
                "culture",
                "Must be an object.",
            )

        culture_fields = {
            "communities": (10, 80),
            "tribes": (10, 80),
            "heritage_tags": (20, 80),
            "festivals": (20, 100),
            "custom_entries": (10, 100),
        }

        unknown = (
            set(culture)
            - set(culture_fields)
        )

        if unknown:
            _fail(
                "culture",
                "Contains unsupported fields.",
            )

        for field, limits in (
            culture_fields.items()
        ):
            if field not in culture:
                continue

            updates[
                f"culture.{field}"
            ] = _string_list(
                culture[field],
                f"culture.{field}",
                max_items=limits[0],
                item_max_length=limits[1],
            )

    if "languages" in patch:
        updates[
            "languages"
        ] = _string_list(
            patch["languages"],
            "languages",
            max_items=10,
            item_max_length=50,
        )

    if "interests" in patch:
        updates[
            "interests"
        ] = _string_list(
            patch["interests"],
            "interests",
            max_items=20,
            item_max_length=50,
        )

    if "prompts" in patch:
        updates[
            "prompts"
        ] = _validate_prompts(
            patch["prompts"]
        )

    if "height_cm" in patch:
        if patch[
            "height_cm"
        ] is None:
            updates[
                "height_cm"
            ] = None
        else:
            updates[
                "height_cm"
            ] = _integer(
                patch["height_cm"],
                "height_cm",
                minimum=100,
                maximum=250,
            )

    for field, mapping in (
        (
            "education",
            {
                "level": 80,
                "institution": 150,
                "field": 120,
            },
        ),
        (
            "work",
            {
                "title": 120,
                "organization": 150,
                "industry": 100,
            },
        ),
        (
            "lifestyle",
            {
                "smoking": 50,
                "drinking": 50,
                "diet": 50,
                "fitness": 50,
                "pets": 50,
            },
        ),
    ):
        if field in patch:
            updates.update(
                _text_mapping_updates(
                    patch[field],
                    field,
                    allowed=mapping,
                )
            )

    if (
        "relationship_intentions"
        in patch
    ):
        intentions = _string_list(
            patch[
                "relationship_intentions"
            ],
            "relationship_intentions",
            max_items=6,
            item_max_length=40,
        )

        unsupported = (
            set(intentions)
            - RELATIONSHIP_INTENTIONS
        )

        if unsupported:
            _fail(
                "relationship_intentions",
                "Contains unsupported values.",
            )

        updates[
            "relationship_intentions"
        ] = intentions

    if "privacy" in patch:
        privacy = patch[
            "privacy"
        ]

        if not isinstance(
            privacy,
            dict,
        ):
            _fail(
                "privacy",
                "Must be an object.",
            )

        allowed_privacy = {
            "show_age",
            "show_distance",
            "show_surname",
            "show_community",
            "show_hometown",
            "show_work",
            "show_education",
        }

        unknown = (
            set(privacy)
            - allowed_privacy
        )

        if unknown:
            _fail(
                "privacy",
                "Contains unsupported fields.",
            )

        for field, value in (
            privacy.items()
        ):
            updates[
                f"privacy.{field}"
            ] = _boolean(
                value,
                f"privacy.{field}",
            )

    return updates


def validate_discovery_patch(
    patch,
    *,
    existing_preferences=None,
):
    if not isinstance(
        patch,
        dict,
    ):
        _fail(
            "discovery",
            "Discovery update must be an object.",
        )

    if not patch:
        _fail(
            "discovery",
            "No discovery fields were provided.",
        )

    allowed = {
        "interested_in",
        "age_min",
        "age_max",
        "distance_enabled",
        "max_distance_km",
        "relationship_intentions",
        "communities",
        "languages",
        "surname_filter",
    }

    unknown = (
        set(patch)
        - allowed
    )

    if unknown:
        _fail(
            "discovery",
            (
                "Unsupported field(s): "
                + ", ".join(
                    sorted(unknown)
                )
            ),
        )

    existing_preferences = (
        existing_preferences
        or {}
    )

    updates = {}

    if "interested_in" in patch:
        values = _string_list(
            patch["interested_in"],
            "interested_in",
            max_items=10,
            item_max_length=40,
        )

        if (
            set(values)
            - DISCOVERY_GENDERS
        ):
            _fail(
                "interested_in",
                "Contains unsupported values.",
            )

        updates[
            "interested_in"
        ] = values

    if "age_min" in patch:
        updates[
            "age_min"
        ] = _integer(
            patch["age_min"],
            "age_min",
            minimum=MINIMUM_USER_AGE,
            maximum=99,
        )

    if "age_max" in patch:
        updates[
            "age_max"
        ] = _integer(
            patch["age_max"],
            "age_max",
            minimum=MINIMUM_USER_AGE,
            maximum=99,
        )

    prospective_min = updates.get(
        "age_min",
        existing_preferences.get(
            "age_min",
            MINIMUM_USER_AGE,
        ),
    )

    prospective_max = updates.get(
        "age_max",
        existing_preferences.get(
            "age_max",
            99,
        ),
    )

    if prospective_min > prospective_max:
        _fail(
            "age_range",
            (
                "Minimum age cannot be "
                "greater than maximum age."
            ),
        )

    if (
        "distance_enabled"
        in patch
    ):
        updates[
            "distance_enabled"
        ] = _boolean(
            patch[
                "distance_enabled"
            ],
            "distance_enabled",
        )

    if (
        "max_distance_km"
        in patch
    ):
        updates[
            "max_distance_km"
        ] = _integer(
            patch[
                "max_distance_km"
            ],
            "max_distance_km",
            minimum=1,
            maximum=500,
        )

    if (
        "relationship_intentions"
        in patch
    ):
        values = _string_list(
            patch[
                "relationship_intentions"
            ],
            "relationship_intentions",
            max_items=6,
            item_max_length=40,
        )

        if (
            set(values)
            - RELATIONSHIP_INTENTIONS
        ):
            _fail(
                "relationship_intentions",
                "Contains unsupported values.",
            )

        updates[
            "relationship_intentions"
        ] = values

    for field in (
        "communities",
        "languages",
    ):
        if field in patch:
            updates[
                field
            ] = _string_list(
                patch[field],
                field,
                max_items=20,
                item_max_length=80,
            )

    if (
        "surname_filter"
        in patch
    ):
        surname_filter = patch[
            "surname_filter"
        ]

        if not isinstance(
            surname_filter,
            dict,
        ):
            _fail(
                "surname_filter",
                "Must be an object.",
            )

        unknown = (
            set(surname_filter)
            - {
                "mode",
                "values",
            }
        )

        if unknown:
            _fail(
                "surname_filter",
                "Contains unsupported fields.",
            )

        existing_filter = (
            existing_preferences.get(
                "surname_filter",
                {},
            )
            or {}
        )

        mode = surname_filter.get(
            "mode",
            existing_filter.get(
                "mode",
                "none",
            ),
        )

        if (
            mode
            not in SURNAME_FILTER_MODES
        ):
            _fail(
                "surname_filter.mode",
                "Unsupported surname filter mode.",
            )

        raw_values = (
            surname_filter.get(
                "values",
                existing_filter.get(
                    "values",
                    [],
                ),
            )
        )

        if not isinstance(
            raw_values,
            list,
        ):
            _fail(
                "surname_filter.values",
                "Must be a list.",
            )

        if len(raw_values) > 20:
            _fail(
                "surname_filter.values",
                (
                    "Cannot contain more than "
                    "20 surnames."
                ),
            )

        normalized_values = []
        seen = set()

        for value in raw_values:
            normalized = (
                normalize_surname(
                    value
                )
            )

            if not normalized:
                continue

            if normalized in seen:
                continue

            seen.add(
                normalized
            )

            normalized_values.append(
                normalized
            )

        if mode == "none":
            normalized_values = []

        elif not normalized_values:
            _fail(
                "surname_filter.values",
                (
                    "Choose at least one surname "
                    "for this filter."
                ),
            )

        updates[
            "surname_filter.mode"
        ] = mode

        updates[
            "surname_filter.values"
        ] = normalized_values

    return updates


def calculate_profile_completion(
    profile,
):
    city = (
        profile.get(
            "current_city"
        )
        or {}
    )

    checks = [
        bool(
            profile.get(
                "display_name"
            )
        ),
        bool(
            profile.get(
                "birth_date"
            )
        ),
        bool(
            profile.get(
                "gender_identity"
            )
        ),
        bool(
            city.get(
                "city"
            )
            and city.get(
                "state"
            )
        ),
        bool(
            profile.get(
                "languages"
            )
        ),
        bool(
            profile.get(
                "bio"
            )
        ),
        len(
            profile.get(
                "interests"
            )
            or []
        ) >= 3,
        bool(
            profile.get(
                "relationship_intentions"
            )
        ),
        bool(
            profile.get(
                "prompts"
            )
        ),
        bool(
            profile.get(
                "media"
            )
        ),
    ]

    completed = sum(
        1
        for value in checks
        if value
    )

    return round(
        (
            completed
            / len(checks)
        )
        * 100
    )
