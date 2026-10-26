"""Tests for Soultie durable Notification schema contracts."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest
from bson import ObjectId

from app.schemas.notification import (
    NOTIFICATION_RESOURCE_TYPES,
    NOTIFICATION_SCHEMA_VERSION,
    NOTIFICATION_TYPES,
    build_notification_document,
    normalize_notification_resource,
    normalize_notification_type,
    serialize_notification_for_user,
)


USER_ID = ObjectId(
    "507f1f77bcf86cd799439011"
)

OTHER_USER_ID = ObjectId(
    "507f1f77bcf86cd799439012"
)

MATCH_ID = ObjectId(
    "507f1f77bcf86cd799439013"
)

NOTIFICATION_ID = ObjectId(
    "507f1f77bcf86cd799439014"
)

FIXED_TIME = datetime(
    2026,
    10,
    14,
    12,
    0,
    tzinfo=timezone.utc,
)


def _notification_document(
    *,
    notification_type="new_match",
    resource_type="match",
    resource_id=MATCH_ID,
):
    document = build_notification_document(
        user_id=USER_ID,
        notification_type=notification_type,
        resource_type=resource_type,
        resource_id=resource_id,
        dedupe_key=(
            "match.created:"
            "507f1f77bcf86cd799439013"
        ),
        now=FIXED_TIME,
    )

    document["_id"] = NOTIFICATION_ID

    return document


def test_notification_constants_are_locked():
    assert NOTIFICATION_SCHEMA_VERSION == 1

    assert NOTIFICATION_TYPES == frozenset(
        {
            "new_match",
            "new_message",
            "profile_interaction",
            "icebreaker",
            "booking_update",
            "booking_reminder",
            "date_reminder",
            "safe_meet_check_in",
            "trusted_contact_alert",
            "safety_alert",
            "hangout_update",
            "event_update",
            "verification_update",
            "report_status",
            "security_alert",
            "system_announcement",
        }
    )

    assert NOTIFICATION_RESOURCE_TYPES == frozenset(
        {
            "match",
            "conversation",
            "profile",
            "reservation",
            "venue",
            "safe_meet",
            "hangout",
            "event",
            "verification",
            "report",
            "account",
        }
    )


def test_notification_type_is_normalized():
    assert (
        normalize_notification_type(
            " NEW_MATCH "
        )
        == "new_match"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "unknown_notification",
        None,
        123,
    ],
)
def test_invalid_notification_type_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_notification_type(
            value
        )


def test_empty_resource_reference_is_supported():
    assert normalize_notification_resource() == (
        None,
        None,
    )


def test_resource_reference_is_normalized():
    result = normalize_notification_resource(
        " MATCH ",
        str(MATCH_ID),
    )

    assert result == (
        "match",
        MATCH_ID,
    )


@pytest.mark.parametrize(
    (
        "resource_type",
        "resource_id",
    ),
    [
        (
            "match",
            None,
        ),
        (
            None,
            MATCH_ID,
        ),
    ],
)
def test_partial_resource_reference_is_rejected(
    resource_type,
    resource_id,
):
    with pytest.raises(
        ValueError
    ):
        normalize_notification_resource(
            resource_type,
            resource_id,
        )


def test_unknown_resource_type_is_rejected():
    with pytest.raises(
        ValueError,
        match=(
            "Unsupported notification "
            "resource type"
        ),
    ):
        normalize_notification_resource(
            "private_culture",
            MATCH_ID,
        )


def test_notification_document_is_privacy_minimal():
    document = _notification_document()

    assert document == {
        "schema_version": 1,
        "user_id": USER_ID,
        "type": "new_match",
        "title": "New match",
        "body": (
            "You have a new connection."
        ),
        "resource_type": "match",
        "resource_id": MATCH_ID,
        "dedupe_key": (
            "match.created:"
            "507f1f77bcf86cd799439013"
        ),
        "created_at": FIXED_TIME,
        "updated_at": FIXED_TIME,
        "read_at": None,
        "dismissed_at": None,
        "_id": NOTIFICATION_ID,
    }


def test_notification_without_resource_is_supported():
    document = build_notification_document(
        user_id=USER_ID,
        notification_type=(
            "system_announcement"
        ),
        now=FIXED_TIME,
    )

    assert document[
        "resource_type"
    ] is None

    assert document[
        "resource_id"
    ] is None


def test_string_recipient_id_is_normalized():
    document = build_notification_document(
        user_id=str(USER_ID),
        notification_type="security_alert",
        now=FIXED_TIME,
    )

    assert document["user_id"] == USER_ID


@pytest.mark.parametrize(
    "user_id",
    [
        "",
        "not-an-object-id",
        None,
        123,
    ],
)
def test_invalid_recipient_id_is_rejected(
    user_id,
):
    with pytest.raises(
        ValueError,
        match=(
            "Invalid notification "
            "recipient ID"
        ),
    ):
        build_notification_document(
            user_id=user_id,
            notification_type=(
                "security_alert"
            ),
            now=FIXED_TIME,
        )


def test_offset_timestamp_is_normalized_to_utc():
    india_time = datetime(
        2026,
        10,
        14,
        17,
        30,
        tzinfo=timezone(
            timedelta(
                hours=5,
                minutes=30,
            )
        ),
    )

    document = build_notification_document(
        user_id=USER_ID,
        notification_type=(
            "security_alert"
        ),
        now=india_time,
    )

    assert document[
        "created_at"
    ] == FIXED_TIME

    assert document[
        "updated_at"
    ] == FIXED_TIME


def test_naive_timestamp_is_treated_as_utc():
    naive_time = datetime(
        2026,
        10,
        14,
        12,
        0,
    )

    document = build_notification_document(
        user_id=USER_ID,
        notification_type="new_message",
        now=naive_time,
    )

    assert (
        document["created_at"].tzinfo
        == timezone.utc
    )

    assert (
        document["created_at"]
        == FIXED_TIME
    )


def test_invalid_timestamp_is_rejected():
    with pytest.raises(
        ValueError,
        match="Invalid notification time",
    ):
        build_notification_document(
            user_id=USER_ID,
            notification_type=(
                "security_alert"
            ),
            now="2026-10-14",
        )


@pytest.mark.parametrize(
    "dedupe_key",
    [
        "",
        "contains whitespace",
        "!" + ("x" * 10),
        "x" * 129,
        123,
    ],
)
def test_invalid_dedupe_key_is_rejected(
    dedupe_key,
):
    with pytest.raises(
        ValueError,
        match=(
            "Invalid notification "
            "dedupe key"
        ),
    ):
        build_notification_document(
            user_id=USER_ID,
            notification_type=(
                "security_alert"
            ),
            dedupe_key=dedupe_key,
            now=FIXED_TIME,
        )


def test_128_character_dedupe_key_is_supported():
    dedupe_key = "a" * 128

    document = build_notification_document(
        user_id=USER_ID,
        notification_type=(
            "security_alert"
        ),
        dedupe_key=dedupe_key,
        now=FIXED_TIME,
    )

    assert (
        document["dedupe_key"]
        == dedupe_key
    )


def test_notification_serialization_is_privacy_minimal():
    document = _notification_document()

    document[
        "title"
    ] = "Tampered private title"

    document[
        "body"
    ] = "Hidden private profile content"

    document[
        "private_culture"
    ] = "MUST_NOT_LEAK"

    document[
        "exact_location"
    ] = "MUST_NOT_LEAK"

    document[
        "actor_profile"
    ] = {
        "name": "MUST_NOT_LEAK",
    }

    payload = (
        serialize_notification_for_user(
            document,
            USER_ID,
        )
    )

    assert payload == {
        "id": str(NOTIFICATION_ID),
        "schema_version": 1,
        "type": "new_match",
        "title": "New match",
        "body": (
            "You have a new connection."
        ),
        "resource_type": "match",
        "resource_id": str(MATCH_ID),
        "created_at": (
            "2026-10-14T12:00:00+00:00"
        ),
        "read_at": None,
        "dismissed_at": None,
        "is_read": False,
    }


def test_serialization_does_not_expose_internal_fields():
    document = _notification_document()

    payload = (
        serialize_notification_for_user(
            document,
            USER_ID,
        )
    )

    for field in (
        "user_id",
        "dedupe_key",
        "updated_at",
    ):
        assert field not in payload


def test_serialization_accepts_string_owner_identity():
    document = _notification_document()

    payload = (
        serialize_notification_for_user(
            document,
            str(USER_ID),
        )
    )

    assert (
        payload["id"]
        == str(NOTIFICATION_ID)
    )


def test_read_and_dismissed_state_is_serialized():
    document = _notification_document()

    read_at = datetime(
        2026,
        10,
        14,
        12,
        5,
        tzinfo=timezone.utc,
    )

    dismissed_at = datetime(
        2026,
        10,
        14,
        12,
        10,
        tzinfo=timezone.utc,
    )

    document["read_at"] = read_at
    document[
        "dismissed_at"
    ] = dismissed_at

    payload = (
        serialize_notification_for_user(
            document,
            USER_ID,
        )
    )

    assert payload["is_read"] is True

    assert payload[
        "read_at"
    ] == (
        "2026-10-14T12:05:00+00:00"
    )

    assert payload[
        "dismissed_at"
    ] == (
        "2026-10-14T12:10:00+00:00"
    )


def test_cross_user_notification_serialization_is_rejected():
    document = _notification_document()

    with pytest.raises(
        ValueError,
        match=(
            "Notification does not "
            "belong to this user"
        ),
    ):
        serialize_notification_for_user(
            document,
            OTHER_USER_ID,
        )


def test_unsupported_schema_version_is_rejected():
    document = _notification_document()

    document[
        "schema_version"
    ] = 999

    with pytest.raises(
        ValueError,
        match=(
            "Unsupported notification "
            "schema version"
        ),
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


@pytest.mark.parametrize(
    "notification",
    [
        None,
        [],
        "not-a-document",
    ],
)
def test_non_document_serialization_is_rejected(
    notification,
):
    with pytest.raises(
        ValueError,
        match=(
            "Invalid notification record"
        ),
    ):
        serialize_notification_for_user(
            notification,
            USER_ID,
        )


def test_missing_notification_id_is_rejected():
    document = _notification_document()

    document.pop("_id")

    with pytest.raises(
        ValueError,
        match=(
            "Invalid notification ID"
        ),
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


def test_malformed_created_at_is_rejected():
    document = _notification_document()

    document[
        "created_at"
    ] = "not-a-timestamp"

    with pytest.raises(
        ValueError,
        match="Invalid created_at",
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


def test_malformed_read_at_is_rejected():
    document = _notification_document()

    document[
        "read_at"
    ] = "not-a-timestamp"

    with pytest.raises(
        ValueError,
        match="Invalid read_at",
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


def test_malformed_dismissed_at_is_rejected():
    document = _notification_document()

    document[
        "dismissed_at"
    ] = "not-a-timestamp"

    with pytest.raises(
        ValueError,
        match="Invalid dismissed_at",
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


def test_stored_unknown_type_fails_closed():
    document = _notification_document()

    document[
        "type"
    ] = "unknown_notification"

    with pytest.raises(
        ValueError,
        match=(
            "Unsupported notification type"
        ),
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )


def test_stored_unknown_resource_fails_closed():
    document = _notification_document()

    document[
        "resource_type"
    ] = "private_internal_resource"

    with pytest.raises(
        ValueError,
        match=(
            "Unsupported notification "
            "resource type"
        ),
    ):
        serialize_notification_for_user(
            document,
            USER_ID,
        )
