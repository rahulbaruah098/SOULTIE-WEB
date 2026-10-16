"""Internal durable background Notification delivery orchestration."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId

from app.repositories.notification_delivery_repository import (
    create_notification_delivery_idempotent,
    suppress_pending_notification_delivery,
)
from app.repositories.notification_preferences_repository import (
    get_notification_preferences_for_user,
)
from app.repositories.notification_repository import (
    list_notifications_for_delivery_reconciliation,
)
from app.schemas.notification import (
    normalize_notification_type,
)
from app.schemas.notification_preferences import (
    notification_channel_is_enabled,
)


BACKGROUND_NOTIFICATION_DELIVERY_CHANNELS = (
    "email",
    "push",
)

CHANNEL_DISABLED_ERROR_CODE = (
    "CHANNEL_DISABLED"
)


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
            "Invalid "
            + field_name
            + "."
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
            "Invalid "
            + field_name
            + "."
        )

    if (
        value.tzinfo
        is None
    ):
        value = value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _notification_identity(
    notification,
    *,
    now=None,
):
    if not isinstance(
        notification,
        dict,
    ):
        raise ValueError(
            "Notification must be an object."
        )

    notification_id = _object_id(
        notification.get(
            "_id"
        ),
        "Notification ID",
    )

    user_id = _object_id(
        notification.get(
            "user_id"
        ),
        "Notification user ID",
    )

    normalize_notification_type(
        notification.get(
            "type"
        )
    )

    timestamp = (
        notification.get(
            "created_at"
        )
        if now is None
        else now
    )

    timestamp = _utc_timestamp(
        timestamp,
        "Notification delivery time",
    )

    return (
        notification_id,
        user_id,
        timestamp,
    )


def provision_notification_deliveries(
    notification,
    *,
    now=None,
):
    """
    Ensure durable email/push delivery state for one Notification.

    Realtime is intentionally excluded: notification.created remains
    an immediate best-effort supplementary signal.
    """

    (
        notification_id,
        user_id,
        timestamp,
    ) = _notification_identity(
        notification,
        now=now,
    )

    preferences = (
        get_notification_preferences_for_user(
            user_id
        )
    )

    channel_results = {}

    for channel in (
        BACKGROUND_NOTIFICATION_DELIVERY_CHANNELS
    ):
        enabled = (
            notification_channel_is_enabled(
                preferences,
                channel,
            )
        )

        result = (
            create_notification_delivery_idempotent(
                notification_id=notification_id,
                user_id=user_id,
                channel=channel,
                now=timestamp,
                next_attempt_at=timestamp,
            )
        )

        delivery = result[
            "delivery"
        ]

        if (
            not enabled
            and delivery.get(
                "status"
            )
            == "pending"
        ):
            suppressed = (
                suppress_pending_notification_delivery(
                    delivery[
                        "_id"
                    ],
                    now=timestamp,
                    error_code=(
                        CHANNEL_DISABLED_ERROR_CODE
                    ),
                )
            )

            if suppressed is not None:
                delivery = suppressed

        channel_results[
            channel
        ] = {
            "created": bool(
                result.get(
                    "created"
                )
            ),
            "enabled": enabled,
            "delivery": delivery,
        }

    return {
        "notification_id": notification_id,
        "user_id": user_id,
        "channels": channel_results,
    }


def reconcile_notification_deliveries(
    *,
    limit,
    after_created_at=None,
    after_notification_id=None,
):
    """
    Repair missing durable delivery rows by scanning durable Notifications.

    The scan is internal/system-only and does not expose global Notification
    access through the HTTP API.
    """

    page = (
        list_notifications_for_delivery_reconciliation(
            limit=limit,
            after_created_at=after_created_at,
            after_notification_id=(
                after_notification_id
            ),
        )
    )

    provisioned = []

    for notification in page[
        "notifications"
    ]:
        provisioned.append(
            provision_notification_deliveries(
                notification
            )
        )

    return {
        "scanned": len(
            page[
                "notifications"
            ]
        ),
        "provisioned": provisioned,
        "has_more": bool(
            page[
                "has_more"
            ]
        ),
        "next_cursor": (
            page[
                "next_cursor"
            ]
        ),
    }


__all__ = [
    "BACKGROUND_NOTIFICATION_DELIVERY_CHANNELS",
    "CHANNEL_DISABLED_ERROR_CODE",
    "provision_notification_deliveries",
    "reconcile_notification_deliveries",
]
