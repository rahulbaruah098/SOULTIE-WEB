"""Durable supplementary delivery state for Xuoroni Notifications."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId


NOTIFICATION_DELIVERY_SCHEMA_VERSION = 1


NOTIFICATION_DELIVERY_CHANNELS = frozenset(
    {
        "realtime",
        "email",
        "push",
    }
)


NOTIFICATION_DELIVERY_STATUSES = frozenset(
    {
        "pending",
        "processing",
        "delivered",
        "failed",
        "suppressed",
    }
)


NOTIFICATION_DELIVERY_TERMINAL_STATUSES = frozenset(
    {
        "delivered",
        "failed",
        "suppressed",
    }
)


NOTIFICATION_DELIVERY_ERROR_CODE_MAX_LENGTH = 64


def _object_id(
    value,
    field_name,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(
                value
            )
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            f"Invalid {field_name}."
        ) from None


def _utc_timestamp(
    value,
    field_name,
):
    if not isinstance(
        value,
        datetime,
    ):
        raise ValueError(
            f"{field_name} must be a datetime."
        )

    if value.tzinfo is None:
        raise ValueError(
            f"{field_name} must be timezone-aware."
        )

    return value.astimezone(
        timezone.utc
    )


def _optional_utc_timestamp(
    value,
    field_name,
):
    if value is None:
        return None

    return _utc_timestamp(
        value,
        field_name,
    )


def _normalize_attempt_count(
    value,
):
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value < 0
    ):
        raise ValueError(
            "Notification delivery attempt_count "
            "must be a non-negative integer."
        )

    return value


def normalize_notification_delivery_channel(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Notification delivery channel must be a string."
        )

    channel = value.strip().lower()

    if (
        channel
        not in NOTIFICATION_DELIVERY_CHANNELS
    ):
        raise ValueError(
            "Unsupported Notification delivery channel."
        )

    return channel


def normalize_notification_delivery_status(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Notification delivery status must be a string."
        )

    status = value.strip().lower()

    if (
        status
        not in NOTIFICATION_DELIVERY_STATUSES
    ):
        raise ValueError(
            "Unsupported Notification delivery status."
        )

    return status


def normalize_notification_delivery_error_code(
    value,
):
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Notification delivery error code must be a string."
        )

    code = value.strip()

    if not code:
        raise ValueError(
            "Notification delivery error code cannot be empty."
        )

    if (
        len(
            code
        )
        > NOTIFICATION_DELIVERY_ERROR_CODE_MAX_LENGTH
    ):
        raise ValueError(
            "Notification delivery error code is too long."
        )

    return code


def build_notification_delivery_document(
    *,
    notification_id,
    user_id,
    channel,
    now=None,
    next_attempt_at=None,
):
    """
    Build the initial durable delivery/outbox record.

    Delivery records never contain Notification body/content.
    They reference the durable Notification by ID and track only
    supplementary channel-dispatch state.
    """

    notification_object_id = _object_id(
        notification_id,
        "notification delivery Notification ID",
    )

    user_object_id = _object_id(
        user_id,
        "notification delivery user ID",
    )

    normalized_channel = (
        normalize_notification_delivery_channel(
            channel
        )
    )

    timestamp = _utc_timestamp(
        (
            datetime.now(
                timezone.utc
            )
            if now is None
            else now
        ),
        "notification delivery time",
    )

    scheduled_at = _optional_utc_timestamp(
        next_attempt_at,
        "notification delivery next_attempt_at",
    )

    if scheduled_at is None:
        scheduled_at = timestamp

    return {
        "schema_version": (
            NOTIFICATION_DELIVERY_SCHEMA_VERSION
        ),
        "notification_id": (
            notification_object_id
        ),
        "user_id": user_object_id,
        "channel": normalized_channel,
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": (
            scheduled_at
        ),
        "lease_expires_at": None,
        "last_attempt_at": None,
        "delivered_at": None,
        "failed_at": None,
        "suppressed_at": None,
        "last_error_code": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def validate_notification_delivery_document(
    delivery,
):
    """
    Validate and normalize a stored delivery state.

    This is intentionally provider-neutral. Firebase, APNS, SMTP,
    Celery and device-token concerns do not belong in this schema.
    """

    if not isinstance(
        delivery,
        dict,
    ):
        raise ValueError(
            "Notification delivery must be a document."
        )

    if (
        delivery.get(
            "schema_version"
        )
        != NOTIFICATION_DELIVERY_SCHEMA_VERSION
    ):
        raise ValueError(
            "Unsupported Notification delivery schema version."
        )

    notification_id = _object_id(
        delivery.get(
            "notification_id"
        ),
        "notification delivery Notification ID",
    )

    user_id = _object_id(
        delivery.get(
            "user_id"
        ),
        "notification delivery user ID",
    )

    channel = (
        normalize_notification_delivery_channel(
            delivery.get(
                "channel"
            )
        )
    )

    status = (
        normalize_notification_delivery_status(
            delivery.get(
                "status"
            )
        )
    )

    attempt_count = (
        _normalize_attempt_count(
            delivery.get(
                "attempt_count"
            )
        )
    )

    created_at = _utc_timestamp(
        delivery.get(
            "created_at"
        ),
        "notification delivery created_at",
    )

    updated_at = _utc_timestamp(
        delivery.get(
            "updated_at"
        ),
        "notification delivery updated_at",
    )

    next_attempt_at = _optional_utc_timestamp(
        delivery.get(
            "next_attempt_at"
        ),
        "notification delivery next_attempt_at",
    )

    lease_expires_at = _optional_utc_timestamp(
        delivery.get(
            "lease_expires_at"
        ),
        "notification delivery lease_expires_at",
    )

    last_attempt_at = _optional_utc_timestamp(
        delivery.get(
            "last_attempt_at"
        ),
        "notification delivery last_attempt_at",
    )

    delivered_at = _optional_utc_timestamp(
        delivery.get(
            "delivered_at"
        ),
        "notification delivery delivered_at",
    )

    failed_at = _optional_utc_timestamp(
        delivery.get(
            "failed_at"
        ),
        "notification delivery failed_at",
    )

    suppressed_at = _optional_utc_timestamp(
        delivery.get(
            "suppressed_at"
        ),
        "notification delivery suppressed_at",
    )

    error_code = (
        normalize_notification_delivery_error_code(
            delivery.get(
                "last_error_code"
            )
        )
    )

    if updated_at < created_at:
        raise ValueError(
            "Notification delivery updated_at "
            "cannot precede created_at."
        )

    if status == "pending":
        if next_attempt_at is None:
            raise ValueError(
                "Pending Notification delivery requires next_attempt_at."
            )

        if lease_expires_at is not None:
            raise ValueError(
                "Pending Notification delivery cannot hold a lease."
            )

        if delivered_at is not None:
            raise ValueError(
                "Pending Notification delivery cannot be delivered."
            )

        if failed_at is not None:
            raise ValueError(
                "Pending Notification delivery cannot be failed."
            )

        if suppressed_at is not None:
            raise ValueError(
                "Pending Notification delivery cannot be suppressed."
            )

    if status == "processing":
        if last_attempt_at is None:
            raise ValueError(
                "Processing Notification delivery requires last_attempt_at."
            )

        if lease_expires_at is None:
            raise ValueError(
                "Processing Notification delivery requires lease_expires_at."
            )

        if delivered_at is not None:
            raise ValueError(
                "Processing Notification delivery cannot be delivered."
            )

        if failed_at is not None:
            raise ValueError(
                "Processing Notification delivery cannot be failed."
            )

        if suppressed_at is not None:
            raise ValueError(
                "Processing Notification delivery cannot be suppressed."
            )

    if status == "delivered":
        if delivered_at is None:
            raise ValueError(
                "Delivered Notification delivery requires delivered_at."
            )

        if next_attempt_at is not None:
            raise ValueError(
                "Delivered Notification delivery cannot remain scheduled."
            )

        if lease_expires_at is not None:
            raise ValueError(
                "Delivered Notification delivery cannot hold a lease."
            )

        if failed_at is not None:
            raise ValueError(
                "Delivered Notification delivery cannot also be failed."
            )

        if suppressed_at is not None:
            raise ValueError(
                "Delivered Notification delivery cannot also be suppressed."
            )

    if status == "failed":
        if failed_at is None:
            raise ValueError(
                "Failed Notification delivery requires failed_at."
            )

        if next_attempt_at is not None:
            raise ValueError(
                "Failed Notification delivery cannot remain scheduled."
            )

        if lease_expires_at is not None:
            raise ValueError(
                "Failed Notification delivery cannot hold a lease."
            )

        if delivered_at is not None:
            raise ValueError(
                "Failed Notification delivery cannot also be delivered."
            )

        if suppressed_at is not None:
            raise ValueError(
                "Failed Notification delivery cannot also be suppressed."
            )

    if status == "suppressed":
        if suppressed_at is None:
            raise ValueError(
                "Suppressed Notification delivery requires suppressed_at."
            )

        if next_attempt_at is not None:
            raise ValueError(
                "Suppressed Notification delivery cannot remain scheduled."
            )

        if lease_expires_at is not None:
            raise ValueError(
                "Suppressed Notification delivery cannot hold a lease."
            )

        if delivered_at is not None:
            raise ValueError(
                "Suppressed Notification delivery cannot also be delivered."
            )

        if failed_at is not None:
            raise ValueError(
                "Suppressed Notification delivery cannot also be failed."
            )

    return {
        "schema_version": (
            NOTIFICATION_DELIVERY_SCHEMA_VERSION
        ),
        "notification_id": (
            notification_id
        ),
        "user_id": user_id,
        "channel": channel,
        "status": status,
        "attempt_count": (
            attempt_count
        ),
        "next_attempt_at": (
            next_attempt_at
        ),
        "lease_expires_at": (
            lease_expires_at
        ),
        "last_attempt_at": (
            last_attempt_at
        ),
        "delivered_at": (
            delivered_at
        ),
        "failed_at": failed_at,
        "suppressed_at": (
            suppressed_at
        ),
        "last_error_code": (
            error_code
        ),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def notification_delivery_is_terminal(
    status,
):
    return (
        normalize_notification_delivery_status(
            status
        )
        in NOTIFICATION_DELIVERY_TERMINAL_STATUSES
    )


__all__ = [
    "NOTIFICATION_DELIVERY_CHANNELS",
    "NOTIFICATION_DELIVERY_ERROR_CODE_MAX_LENGTH",
    "NOTIFICATION_DELIVERY_SCHEMA_VERSION",
    "NOTIFICATION_DELIVERY_STATUSES",
    "NOTIFICATION_DELIVERY_TERMINAL_STATUSES",
    "build_notification_delivery_document",
    "normalize_notification_delivery_channel",
    "normalize_notification_delivery_error_code",
    "normalize_notification_delivery_status",
    "notification_delivery_is_terminal",
    "validate_notification_delivery_document",
]
