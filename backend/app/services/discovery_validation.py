"""Validation helpers for Soultie discovery requests."""

import base64
import binascii
import json
from datetime import datetime, timezone

from bson import ObjectId

from app.schemas.discovery import (
    DISCOVERY_DEFAULT_LIMIT,
    DISCOVERY_MAX_CURSOR_LENGTH,
    DISCOVERY_MAX_LIMIT,
)


class DiscoveryValidationError(
    ValueError
):
    def __init__(
        self,
        field,
        message,
    ):
        self.field = field
        self.message = message

        super().__init__(
            message
        )


def _fail(
    field,
    message,
):
    raise DiscoveryValidationError(
        field,
        message,
    )


def validate_limit(
    value,
):
    if value is None:
        return (
            DISCOVERY_DEFAULT_LIMIT
        )

    if isinstance(
        value,
        bool,
    ):
        _fail(
            "limit",
            "Limit must be an integer.",
        )

    try:
        parsed = int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        _fail(
            "limit",
            "Limit must be an integer.",
        )

    if (
        parsed < 1
        or parsed > DISCOVERY_MAX_LIMIT
    ):
        _fail(
            "limit",
            (
                f"Limit must be between "
                f"1 and {DISCOVERY_MAX_LIMIT}."
            ),
        )

    return parsed


def _normalize_cursor_datetime(
    value,
    *,
    allow_naive_utc=False,
):
    if not isinstance(
        value,
        datetime,
    ):
        _fail(
            "cursor",
            (
                "Discovery cursor requires "
                "a datetime value."
            ),
        )

    if (
        value.tzinfo
        is None
        or value.utcoffset()
        is None
    ):
        if not allow_naive_utc:
            _fail(
                "cursor",
                (
                    "Discovery cursor datetime "
                    "must include a timezone."
                ),
            )

        # PyMongo returns BSON UTC datetimes as naive UTC values
        # unless tz_aware is enabled on the client.
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def encode_discovery_cursor(
    *,
    last_active_at,
    profile_id,
):
    last_active_at = (
        _normalize_cursor_datetime(
            last_active_at,
            allow_naive_utc=True,
        )
    )

    if not isinstance(
        profile_id,
        ObjectId,
    ):
        try:
            profile_id = ObjectId(
                str(profile_id)
            )

        except (
            TypeError,
            ValueError,
        ):
            _fail(
                "cursor",
                "Invalid discovery profile ID.",
            )

    payload = {
        "last_active_at": (
            last_active_at.isoformat()
        ),
        "profile_id": str(
            profile_id
        ),
    }

    raw = json.dumps(
        payload,
        separators=(
            ",",
            ":",
        ),
        sort_keys=True,
    ).encode(
        "utf-8"
    )

    return base64.urlsafe_b64encode(
        raw
    ).decode(
        "ascii"
    ).rstrip(
        "="
    )


def decode_discovery_cursor(
    value,
):
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        _fail(
            "cursor",
            "Cursor must be text.",
        )

    value = value.strip()

    if not value:
        return None

    if (
        len(value)
        > DISCOVERY_MAX_CURSOR_LENGTH
    ):
        _fail(
            "cursor",
            "Cursor is too long.",
        )

    padding = (
        "="
        * (
            -len(value)
            % 4
        )
    )

    try:
        raw = (
            base64.urlsafe_b64decode(
                value
                + padding
            )
        )

        payload = json.loads(
            raw.decode(
                "utf-8"
            )
        )

    except (
        binascii.Error,
        ValueError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    if not isinstance(
        payload,
        dict,
    ):
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    allowed = {
        "last_active_at",
        "profile_id",
    }

    if (
        set(payload)
        != allowed
    ):
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    raw_last_active_at = (
        payload.get(
            "last_active_at"
        )
    )

    if not isinstance(
        raw_last_active_at,
        str,
    ) or not raw_last_active_at.strip():
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    try:
        last_active_at = (
            datetime.fromisoformat(
                raw_last_active_at
            )
        )

    except ValueError:
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    last_active_at = (
        _normalize_cursor_datetime(
            last_active_at,
            allow_naive_utc=True,
        )
    )

    try:
        object_id = ObjectId(
            str(
                payload.get(
                    "profile_id"
                )
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        _fail(
            "cursor",
            "Invalid discovery cursor.",
        )

    return {
        "last_active_at": (
            last_active_at
        ),
        "profile_id": object_id,
    }


def validate_discovery_query(
    *,
    limit=None,
    cursor=None,
):
    return {
        "limit": validate_limit(
            limit
        ),
        "cursor": (
            decode_discovery_cursor(
                cursor
            )
        ),
    }


__all__ = [
    "DiscoveryValidationError",
    "decode_discovery_cursor",
    "encode_discovery_cursor",
    "validate_discovery_query",
    "validate_limit",
]
