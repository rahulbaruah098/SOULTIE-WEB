"""Tests for Soultie discovery request validation."""

from datetime import (
    datetime,
    timezone,
)

import pytest
from bson import ObjectId

from app.services.discovery_validation import (
    DiscoveryValidationError,
    decode_discovery_cursor,
    encode_discovery_cursor,
    validate_discovery_query,
    validate_limit,
)


def test_default_discovery_limit():
    assert validate_limit(
        None
    ) == 20


@pytest.mark.parametrize(
    "value",
    [
        1,
        20,
        50,
        "10",
    ],
)
def test_valid_discovery_limits(
    value,
):
    result = validate_limit(
        value
    )

    assert (
        1
        <= result
        <= 50
    )


@pytest.mark.parametrize(
    "value",
    [
        0,
        51,
        -1,
        "abc",
        True,
    ],
)
def test_invalid_discovery_limits(
    value,
):
    with pytest.raises(
        DiscoveryValidationError
    ) as exc:
        validate_limit(
            value
        )

    assert (
        exc.value.field
        == "limit"
    )


def test_cursor_round_trip():
    profile_id = ObjectId()

    last_active_at = datetime(
        2026,
        10,
        3,
        12,
        30,
        tzinfo=timezone.utc,
    )

    cursor = encode_discovery_cursor(
        last_active_at=last_active_at,
        profile_id=profile_id,
    )

    decoded = (
        decode_discovery_cursor(
            cursor
        )
    )

    assert (
        decoded[
            "profile_id"
        ]
        == profile_id
    )

    assert (
        decoded[
            "last_active_at"
        ]
        == last_active_at
    )


def test_invalid_cursor_is_rejected():
    with pytest.raises(
        DiscoveryValidationError
    ) as exc:
        decode_discovery_cursor(
            "%%%not-a-valid-cursor%%%"
        )

    assert (
        exc.value.field
        == "cursor"
    )


def test_naive_mongodb_datetime_is_treated_as_utc():
    profile_id = ObjectId()

    naive_utc = datetime(
        2026,
        10,
        3,
        12,
        30,
    )

    cursor = encode_discovery_cursor(
        last_active_at=naive_utc,
        profile_id=profile_id,
    )

    decoded = decode_discovery_cursor(
        cursor
    )

    assert (
        decoded[
            "profile_id"
        ]
        == profile_id
    )

    assert (
        decoded[
            "last_active_at"
        ]
        == naive_utc.replace(
            tzinfo=timezone.utc
        )
    )


def test_query_validation_combines_limit_and_cursor():
    profile_id = ObjectId()

    cursor = encode_discovery_cursor(
        last_active_at=datetime(
            2026,
            10,
            3,
            12,
            30,
            tzinfo=timezone.utc,
        ),
        profile_id=profile_id,
    )

    result = (
        validate_discovery_query(
            limit="15",
            cursor=cursor,
        )
    )

    assert result[
        "limit"
    ] == 15

    assert (
        result[
            "cursor"
        ][
            "profile_id"
        ]
        == profile_id
    )
