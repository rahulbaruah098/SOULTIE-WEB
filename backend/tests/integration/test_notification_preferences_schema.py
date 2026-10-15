from datetime import (
    datetime,
    timezone,
)

import pytest
from bson import ObjectId

from app.schemas.notification import (
    NOTIFICATION_TYPES,
)
from app.schemas.notification_preferences import (
    DISABLEABLE_IN_APP_NOTIFICATION_TYPES,
    MANDATORY_IN_APP_NOTIFICATION_TYPES,
    NOTIFICATION_PREFERENCES_SCHEMA_VERSION,
    build_notification_preferences_document,
    normalize_disabled_notification_types,
    normalize_notification_preferences_patch,
    notification_type_is_enabled,
    serialize_notification_preferences_for_owner,
)


def test_notification_preferences_type_boundaries():
    assert (
        NOTIFICATION_PREFERENCES_SCHEMA_VERSION
        == 1
    )

    assert (
        MANDATORY_IN_APP_NOTIFICATION_TYPES
        <= NOTIFICATION_TYPES
    )

    assert (
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
        == (
            NOTIFICATION_TYPES
            - MANDATORY_IN_APP_NOTIFICATION_TYPES
        )
    )

    assert (
        MANDATORY_IN_APP_NOTIFICATION_TYPES
        & DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    ) == set()


def test_notification_preferences_mandatory_types():
    assert (
        MANDATORY_IN_APP_NOTIFICATION_TYPES
        == frozenset(
            {
                "safe_meet_check_in",
                "trusted_contact_alert",
                "security_alert",
            }
        )
    )


def test_build_notification_preferences_defaults():
    user_id = ObjectId()

    now = datetime(
        2026,
        10,
        15,
        12,
        30,
        tzinfo=timezone.utc,
    )

    document = (
        build_notification_preferences_document(
            user_id=user_id,
            now=now,
        )
    )

    assert document == {
        "schema_version": 1,
        "user_id": user_id,
        "disabled_types": [],
        "created_at": now,
        "updated_at": now,
    }


def test_build_notification_preferences_normalizes_string_user_id():
    user_id = ObjectId()

    document = (
        build_notification_preferences_document(
            user_id=str(
                user_id
            ),
        )
    )

    assert (
        document[
            "user_id"
        ]
        == user_id
    )


@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        "not-an-object-id",
        123,
    ],
)
def test_build_notification_preferences_rejects_invalid_user_id(
    user_id,
):
    with pytest.raises(
        ValueError,
        match=(
            "Invalid notification "
            "preferences user ID"
        ),
    ):
        build_notification_preferences_document(
            user_id=user_id,
        )


def test_disabled_notification_types_are_canonicalized():
    disableable = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    assert len(
        disableable
    ) >= 2

    first = disableable[0]
    second = disableable[1]

    result = (
        normalize_disabled_notification_types(
            [
                second,
                f" {first.upper()} ",
                first,
                second,
            ]
        )
    )

    assert result == sorted(
        [
            first,
            second,
        ]
    )


@pytest.mark.parametrize(
    "value",
    [
        "new_message",
        {
            "new_message": True,
        },
        (
            "new_message",
        ),
        1,
    ],
)
def test_disabled_notification_types_require_array(
    value,
):
    with pytest.raises(
        ValueError,
        match=(
            "disabled_types must "
            "be an array"
        ),
    ):
        normalize_disabled_notification_types(
            value
        )


@pytest.mark.parametrize(
    "notification_type",
    sorted(
        MANDATORY_IN_APP_NOTIFICATION_TYPES
    ),
)
def test_mandatory_notification_types_cannot_be_disabled(
    notification_type,
):
    with pytest.raises(
        ValueError,
        match=(
            "Mandatory notification types "
            "cannot be disabled"
        ),
    ):
        normalize_disabled_notification_types(
            [
                notification_type,
            ]
        )


def test_unknown_notification_type_cannot_be_disabled():
    with pytest.raises(
        ValueError,
        match=(
            "Unsupported disabled "
            "notification type"
        ),
    ):
        normalize_disabled_notification_types(
            [
                "unknown_notification_type",
            ]
        )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "invalid",
        {},
        {
            "push_enabled": False,
        },
        {
            "disabled_types": [],
            "email_enabled": False,
        },
    ],
)
def test_notification_preferences_patch_is_strict(
    payload,
):
    with pytest.raises(
        ValueError
    ):
        normalize_notification_preferences_patch(
            payload
        )


def test_notification_preferences_patch_normalizes_disabled_types():
    disableable = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    notification_type = disableable[0]

    result = (
        normalize_notification_preferences_patch(
            {
                "disabled_types": [
                    f" {notification_type.upper()} ",
                    notification_type,
                ],
            }
        )
    )

    assert result == {
        "disabled_types": [
            notification_type,
        ],
    }


def test_notification_type_enablement_semantics():
    disableable = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    notification_type = disableable[0]

    preferences = (
        build_notification_preferences_document(
            user_id=ObjectId(),
            disabled_types=[
                notification_type,
            ],
        )
    )

    assert (
        notification_type_is_enabled(
            preferences,
            notification_type,
        )
        is False
    )

    assert (
        notification_type_is_enabled(
            None,
            notification_type,
        )
        is True
    )

    for mandatory_type in (
        MANDATORY_IN_APP_NOTIFICATION_TYPES
    ):
        assert (
            notification_type_is_enabled(
                preferences,
                mandatory_type,
            )
            is True
        )


def test_owner_serialization_is_privacy_minimal():
    disableable = sorted(
        DISABLEABLE_IN_APP_NOTIFICATION_TYPES
    )

    notification_type = disableable[0]

    preferences = (
        build_notification_preferences_document(
            user_id=ObjectId(),
            disabled_types=[
                notification_type,
            ],
        )
    )

    payload = (
        serialize_notification_preferences_for_owner(
            preferences
        )
    )

    assert payload == {
        "schema_version": 1,
        "in_app": {
            "disabled_types": [
                notification_type,
            ],
            "mandatory_types": sorted(
                MANDATORY_IN_APP_NOTIFICATION_TYPES
            ),
        },
    }

    assert (
        "user_id"
        not in payload
    )

    assert (
        "created_at"
        not in payload
    )

    assert (
        "updated_at"
        not in payload
    )


def test_owner_serialization_of_missing_preferences_is_none():
    assert (
        serialize_notification_preferences_for_owner(
            None
        )
        is None
    )
