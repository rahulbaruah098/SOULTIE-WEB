"""Canonical in-app Notification Preferences schema for Xuoroni."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId

from app.schemas.notification import (
    NOTIFICATION_TYPES,
    normalize_notification_type,
)


NOTIFICATION_PREFERENCES_SCHEMA_VERSION = 1


# Safety and account-security notifications must never be disabled by a
# user-level preference. These remain available even when other in-app
# notification types are muted.
MANDATORY_IN_APP_NOTIFICATION_TYPES = frozenset(
    {
        "safe_meet_check_in",
        "trusted_contact_alert",
        "security_alert",
    }
)


if not MANDATORY_IN_APP_NOTIFICATION_TYPES.issubset(
    NOTIFICATION_TYPES
):
    raise RuntimeError(
        "Mandatory Notification Preference types "
        "must exist in NOTIFICATION_TYPES."
    )


DISABLEABLE_IN_APP_NOTIFICATION_TYPES = frozenset(
    NOTIFICATION_TYPES
    - MANDATORY_IN_APP_NOTIFICATION_TYPES
)


def _object_id(
    value,
    field,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            f"Invalid {field}."
        )

    try:
        return ObjectId(
            value.strip()
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            f"Invalid {field}."
        ) from None


def _utc_timestamp(
    value,
    field,
):
    if not isinstance(
        value,
        datetime,
    ):
        raise ValueError(
            f"Invalid {field}."
        )

    if value.tzinfo is None:
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def normalize_disabled_notification_types(
    value,
):
    """Validate and canonicalize user-disabled in-app Notification types."""

    if value is None:
        return []

    if not isinstance(
        value,
        list,
    ):
        raise ValueError(
            "disabled_types must be an array."
        )

    normalized = []
    seen = set()

    for item in value:
        try:
            notification_type = (
                normalize_notification_type(
                    item
                )
            )

        except ValueError:
            raise ValueError(
                "Unsupported disabled notification type."
            ) from None

        if (
            notification_type
            in MANDATORY_IN_APP_NOTIFICATION_TYPES
        ):
            raise ValueError(
                "Mandatory notification types "
                "cannot be disabled."
            )

        if notification_type in seen:
            continue

        seen.add(
            notification_type
        )

        normalized.append(
            notification_type
        )

    return sorted(
        normalized
    )


def build_notification_preferences_document(
    *,
    user_id,
    disabled_types=None,
    now=None,
):
    """Build one canonical per-user in-app Notification Preferences record."""

    owner_id = _object_id(
        user_id,
        "notification preferences user ID",
    )

    timestamp = _utc_timestamp(
        (
            datetime.now(
                timezone.utc
            )
            if now is None
            else now
        ),
        "notification preferences time",
    )

    return {
        "schema_version": (
            NOTIFICATION_PREFERENCES_SCHEMA_VERSION
        ),
        "user_id": owner_id,
        "disabled_types": (
            normalize_disabled_notification_types(
                disabled_types
            )
        ),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def normalize_notification_preferences_patch(
    payload,
):
    """Validate the current owner-editable Notification Preferences payload."""

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "Notification preferences body "
            "must be an object."
        )

    allowed = {
        "disabled_types",
    }

    unknown = (
        set(
            payload
        )
        - allowed
    )

    if unknown:
        raise ValueError(
            "Unsupported Notification Preferences field."
        )

    if (
        "disabled_types"
        not in payload
    ):
        raise ValueError(
            "At least one Notification Preferences "
            "field is required."
        )

    return {
        "disabled_types": (
            normalize_disabled_notification_types(
                payload[
                    "disabled_types"
                ]
            )
        ),
    }


def notification_type_is_enabled(
    preferences,
    notification_type,
):
    """Return whether an in-app Notification type is enabled for the user."""

    kind = normalize_notification_type(
        notification_type
    )

    if (
        kind
        in MANDATORY_IN_APP_NOTIFICATION_TYPES
    ):
        return True

    if preferences is None:
        return True

    disabled = set(
        normalize_disabled_notification_types(
            preferences.get(
                "disabled_types",
                [],
            )
        )
    )

    return (
        kind
        not in disabled
    )


def serialize_notification_preferences_for_owner(
    preferences,
):
    """Return the privacy-minimal owner-facing Notification Preferences."""

    if preferences is None:
        return None

    return {
        "schema_version": (
            preferences.get(
                "schema_version",
                NOTIFICATION_PREFERENCES_SCHEMA_VERSION,
            )
        ),
        "in_app": {
            "disabled_types": (
                normalize_disabled_notification_types(
                    preferences.get(
                        "disabled_types",
                        [],
                    )
                )
            ),
            "mandatory_types": sorted(
                MANDATORY_IN_APP_NOTIFICATION_TYPES
            ),
        },
    }


__all__ = [
    "NOTIFICATION_PREFERENCES_SCHEMA_VERSION",
    "MANDATORY_IN_APP_NOTIFICATION_TYPES",
    "DISABLEABLE_IN_APP_NOTIFICATION_TYPES",
    "build_notification_preferences_document",
    "normalize_disabled_notification_types",
    "normalize_notification_preferences_patch",
    "notification_type_is_enabled",
    "serialize_notification_preferences_for_owner",
]
