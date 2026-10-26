"""Validation contracts for Soultie cafe venue profiles.

This module validates public input but does not perform
authentication, approval, or database modifications.
"""

import re

VENUE_STATUS_PENDING = "pending"
VENUE_STATUS_APPROVED = "approved"
VENUE_STATUS_REJECTED = "rejected"
VENUE_STATUS_SUSPENDED = "suspended"

VENUE_STATUSES = frozenset({
    VENUE_STATUS_PENDING,
    VENUE_STATUS_APPROVED,
    VENUE_STATUS_REJECTED,
    VENUE_STATUS_SUSPENDED,
})

REGISTRATION_FIELDS = frozenset({
    "owner_name",
    "owner_email",
    "owner_phone",
    "venue_name",
    "address",
    "city",
    "state",
    "postal_code",
})

PROFILE_FIELDS = frozenset({
    "venue_name",
    "description",
    "address",
    "city",
    "state",
    "postal_code",
    "contact_phone",
    "website",
})

MAX_LENGTHS = {
    "owner_name": 100,
    "owner_email": 254,
    "owner_phone": 20,
    "venue_name": 150,
    "description": 2000,
    "address": 300,
    "city": 100,
    "state": 100,
    "postal_code": 12,
    "contact_phone": 20,
    "website": 300,
}

EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$"
)

PHONE_PATTERN = re.compile(
    r"^\+?[0-9][0-9 -]{6,18}[0-9]$"
)

POSTAL_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9 -]{2,11}$"
)


class VenueValidationError(ValueError):
    """Invalid or unsupported venue data."""


def _validate_mapping(data, allowed):
    if not isinstance(data, dict):
        raise VenueValidationError("Expected a JSON object.")

    unexpected = set(data) - allowed

    if unexpected:
        raise VenueValidationError(
            "Unsupported venue fields: "
            + ", ".join(sorted(str(x) for x in unexpected))
        )


def _text(data, field, *, required=False):
    value = data.get(field)

    if value is None:
        if required:
            raise VenueValidationError(
                f"{field} is required."
            )
        return None

    if not isinstance(value, str):
        raise VenueValidationError(
            f"{field} must be text."
        )

    value = value.strip()

    if required and not value:
        raise VenueValidationError(
            f"{field} is required."
        )

    if len(value) > MAX_LENGTHS[field]:
        raise VenueValidationError(
            f"{field} exceeds the maximum length."
        )

    if any(
        ord(char) < 32 or ord(char) == 127
        for char in value
    ):
        raise VenueValidationError(
            f"{field} contains invalid control characters."
        )

    return value


def _validate_email(value):
    normalized = value.lower()

    if not EMAIL_PATTERN.fullmatch(normalized):
        raise VenueValidationError(
            "Please provide a valid owner_email."
        )

    return normalized


def _validate_phone(value, field):
    if not PHONE_PATTERN.fullmatch(value):
        raise VenueValidationError(
            f"Please provide a valid {field}."
        )

    return value


def _validate_postal(value):
    if value and not POSTAL_PATTERN.fullmatch(value):
        raise VenueValidationError(
            "Please provide a valid postal_code."
        )

    return value


def validate_cafe_registration(data):
    """Return clean registration data without assigning approval."""
    _validate_mapping(data, REGISTRATION_FIELDS)

    required = (
        "owner_name",
        "owner_email",
        "owner_phone",
        "venue_name",
        "address",
        "city",
        "state",
    )

    result = {
        field: _text(data, field, required=True)
        for field in required
    }

    result["owner_email"] = _validate_email(
        result["owner_email"]
    )

    result["owner_phone"] = _validate_phone(
        result["owner_phone"],
        "owner_phone",
    )

    postal = _text(data, "postal_code")

    if postal:
        result["postal_code"] = _validate_postal(postal)

    return result


def validate_venue_profile(data):
    """Validate a partial cafe venue-profile update."""
    _validate_mapping(data, PROFILE_FIELDS)

    if not data:
        raise VenueValidationError(
            "At least one venue field is required."
        )

    result = {}

    for field in sorted(data):
        value = _text(
            data,
            field,
            required=field in {
                "venue_name",
                "address",
                "city",
                "state",
            },
        )

        if value is None:
            raise VenueValidationError(
                f"{field} cannot be null."
            )

        if field == "contact_phone" and value:
            value = _validate_phone(value, field)

        if field == "postal_code" and value:
            value = _validate_postal(value)

        if field == "website" and value:
            if not (
                value.startswith("https://")
                and len(value) > len("https://")
            ):
                raise VenueValidationError(
                    "website must use HTTPS."
                )

        result[field] = value

    return result
