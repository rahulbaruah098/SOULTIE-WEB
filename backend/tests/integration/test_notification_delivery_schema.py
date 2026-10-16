from datetime import (
    datetime,
    timedelta,
    timezone,
)

from bson import ObjectId
import pytest

from app.schemas.notification_delivery import (
    NOTIFICATION_DELIVERY_CHANNELS,
    NOTIFICATION_DELIVERY_SCHEMA_VERSION,
    NOTIFICATION_DELIVERY_STATUSES,
    NOTIFICATION_DELIVERY_TERMINAL_STATUSES,
    build_notification_delivery_document,
    normalize_notification_delivery_channel,
    normalize_notification_delivery_error_code,
    normalize_notification_delivery_status,
    notification_delivery_is_terminal,
    validate_notification_delivery_document,
)


BASE_TIME = datetime(
    2026,
    10,
    16,
    5,
    0,
    tzinfo=timezone.utc,
)


def _pending_delivery(
    *,
    channel="realtime",
):
    return build_notification_delivery_document(
        notification_id=ObjectId(
            "507f1f77bcf86cd799439011"
        ),
        user_id=ObjectId(
            "507f1f77bcf86cd799439012"
        ),
        channel=channel,
        now=BASE_TIME,
    )


def test_notification_delivery_constants_are_locked():
    assert (
        NOTIFICATION_DELIVERY_SCHEMA_VERSION
        == 1
    )

    assert NOTIFICATION_DELIVERY_CHANNELS == {
        "realtime",
        "email",
        "push",
    }

    assert NOTIFICATION_DELIVERY_STATUSES == {
        "pending",
        "processing",
        "delivered",
        "failed",
        "suppressed",
    }

    assert (
        NOTIFICATION_DELIVERY_TERMINAL_STATUSES
        == {
            "delivered",
            "failed",
            "suppressed",
        }
    )


def test_notification_delivery_builder_creates_canonical_pending_record():
    delivery = _pending_delivery()

    assert (
        delivery[
            "schema_version"
        ]
        == NOTIFICATION_DELIVERY_SCHEMA_VERSION
    )

    assert (
        delivery[
            "channel"
        ]
        == "realtime"
    )

    assert (
        delivery[
            "status"
        ]
        == "pending"
    )

    assert (
        delivery[
            "attempt_count"
        ]
        == 0
    )

    assert (
        delivery[
            "next_attempt_at"
        ]
        == BASE_TIME
    )

    assert (
        delivery[
            "lease_expires_at"
        ]
        is None
    )

    assert (
        delivery[
            "last_attempt_at"
        ]
        is None
    )

    assert (
        delivery[
            "delivered_at"
        ]
        is None
    )

    assert (
        delivery[
            "failed_at"
        ]
        is None
    )

    assert (
        delivery[
            "suppressed_at"
        ]
        is None
    )

    assert (
        delivery[
            "last_error_code"
        ]
        is None
    )


def test_notification_delivery_builder_supports_future_scheduling():
    next_attempt = (
        BASE_TIME
        + timedelta(
            minutes=10
        )
    )

    delivery = (
        build_notification_delivery_document(
            notification_id=ObjectId(),
            user_id=ObjectId(),
            channel=" PUSH ",
            now=BASE_TIME,
            next_attempt_at=(
                next_attempt
            ),
        )
    )

    assert (
        delivery[
            "channel"
        ]
        == "push"
    )

    assert (
        delivery[
            "next_attempt_at"
        ]
        == next_attempt
    )


def test_notification_delivery_channel_normalization_fails_closed():
    assert (
        normalize_notification_delivery_channel(
            " EMAIL "
        )
        == "email"
    )

    for invalid in (
        None,
        123,
        "",
        "sms",
        "firebase",
        "apns",
    ):
        with pytest.raises(
            ValueError
        ):
            normalize_notification_delivery_channel(
                invalid
            )


def test_notification_delivery_status_normalization_fails_closed():
    assert (
        normalize_notification_delivery_status(
            " PROCESSING "
        )
        == "processing"
    )

    for invalid in (
        None,
        123,
        "",
        "queued",
        "sending",
        "retrying",
    ):
        with pytest.raises(
            ValueError
        ):
            normalize_notification_delivery_status(
                invalid
            )


def test_notification_delivery_error_code_is_privacy_safe_machine_value():
    assert (
        normalize_notification_delivery_error_code(
            " TRANSPORT_UNAVAILABLE "
        )
        == "TRANSPORT_UNAVAILABLE"
    )

    assert (
        normalize_notification_delivery_error_code(
            None
        )
        is None
    )

    for invalid in (
        "",
        "   ",
        123,
        "X" * 65,
    ):
        with pytest.raises(
            ValueError
        ):
            normalize_notification_delivery_error_code(
                invalid
            )


def test_pending_delivery_validation_requires_retry_schedule():
    delivery = _pending_delivery()

    validated = (
        validate_notification_delivery_document(
            delivery
        )
    )

    assert (
        validated[
            "status"
        ]
        == "pending"
    )

    invalid = dict(
        delivery
    )

    invalid[
        "next_attempt_at"
    ] = None

    with pytest.raises(
        ValueError
    ):
        validate_notification_delivery_document(
            invalid
        )


def test_processing_delivery_requires_crash_recovery_lease():
    delivery = _pending_delivery()

    delivery[
        "status"
    ] = "processing"

    delivery[
        "attempt_count"
    ] = 1

    delivery[
        "last_attempt_at"
    ] = BASE_TIME

    delivery[
        "lease_expires_at"
    ] = (
        BASE_TIME
        + timedelta(
            minutes=5
        )
    )

    validated = (
        validate_notification_delivery_document(
            delivery
        )
    )

    assert (
        validated[
            "status"
        ]
        == "processing"
    )

    assert (
        validated[
            "lease_expires_at"
        ]
        > BASE_TIME
    )

    delivery[
        "lease_expires_at"
    ] = None

    with pytest.raises(
        ValueError
    ):
        validate_notification_delivery_document(
            delivery
        )


def test_delivered_delivery_is_terminal_and_unscheduled():
    delivery = _pending_delivery()

    delivery[
        "status"
    ] = "delivered"

    delivery[
        "next_attempt_at"
    ] = None

    delivery[
        "delivered_at"
    ] = (
        BASE_TIME
        + timedelta(
            seconds=2
        )
    )

    validated = (
        validate_notification_delivery_document(
            delivery
        )
    )

    assert (
        validated[
            "status"
        ]
        == "delivered"
    )

    assert (
        notification_delivery_is_terminal(
            "delivered"
        )
        is True
    )

    assert (
        notification_delivery_is_terminal(
            "pending"
        )
        is False
    )


def test_failed_delivery_is_terminal_with_machine_error_code():
    delivery = _pending_delivery()

    delivery[
        "status"
    ] = "failed"

    delivery[
        "next_attempt_at"
    ] = None

    delivery[
        "failed_at"
    ] = (
        BASE_TIME
        + timedelta(
            minutes=1
        )
    )

    delivery[
        "last_error_code"
    ] = "TRANSPORT_UNAVAILABLE"

    validated = (
        validate_notification_delivery_document(
            delivery
        )
    )

    assert (
        validated[
            "status"
        ]
        == "failed"
    )

    assert (
        validated[
            "last_error_code"
        ]
        == "TRANSPORT_UNAVAILABLE"
    )

    assert (
        notification_delivery_is_terminal(
            "failed"
        )
        is True
    )


def test_suppressed_delivery_is_terminal_without_transport_attempt():
    delivery = _pending_delivery()

    delivery[
        "status"
    ] = "suppressed"

    delivery[
        "next_attempt_at"
    ] = None

    delivery[
        "suppressed_at"
    ] = BASE_TIME

    validated = (
        validate_notification_delivery_document(
            delivery
        )
    )

    assert (
        validated[
            "status"
        ]
        == "suppressed"
    )

    assert (
        validated[
            "attempt_count"
        ]
        == 0
    )

    assert (
        notification_delivery_is_terminal(
            "suppressed"
        )
        is True
    )


def test_delivery_document_does_not_duplicate_notification_or_provider_secrets():
    delivery = _pending_delivery()

    assert set(
        delivery
    ) == {
        "schema_version",
        "notification_id",
        "user_id",
        "channel",
        "status",
        "attempt_count",
        "next_attempt_at",
        "lease_expires_at",
        "last_attempt_at",
        "delivered_at",
        "failed_at",
        "suppressed_at",
        "last_error_code",
        "created_at",
        "updated_at",
    }

    for forbidden in (
        "title",
        "body",
        "dedupe_key",
        "resource_type",
        "resource_id",
        "message_body",
        "device_token",
        "push_token",
        "fcm_token",
        "apns_token",
        "provider_payload",
    ):
        assert (
            forbidden
            not in delivery
        )
