"""Canonical privacy-minimal durable Notification schema for Soultie.

Schema builders cannot grant access to a match, chat, or resource.
Producers and readers must recheck authorization and lifecycle state.
"""

import re
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId


NOTIFICATION_SCHEMA_VERSION = 1

# Generic text avoids storing names, messages, culture or location.
NOTIFICATION_COPY = {
    "new_match": ("New match", "You have a new connection."),
    "new_message": ("New message", "You have a message to view."),
    "profile_interaction": ("Profile update", "You have a new profile update."),
    "icebreaker": ("Conversation update", "A conversation starter is ready."),
    "booking_update": ("Booking update", "Your booking has an update."),
    "booking_reminder": ("Booking reminder", "You have an upcoming booking."),
    "date_reminder": ("Meet reminder", "You have an upcoming meet."),
    "safe_meet_check_in": ("Safety check-in", "A safety check-in needs attention."),
    "trusted_contact_alert": ("Safety alert", "A trusted-contact update is available."),
    "safety_alert": ("Safety update", "A safety update needs attention."),
    "hangout_update": ("Hangout update", "A hangout has an update."),
    "event_update": ("Event update", "An event has an update."),
    "verification_update": ("Verification update", "Your verification status has changed."),
    "report_status": ("Report update", "An update about your report is available."),
    "security_alert": ("Account security", "An account security update is available."),
    "system_announcement": ("Xuoroni update", "A platform update is available."),
}

NOTIFICATION_TYPES = frozenset(NOTIFICATION_COPY)

NOTIFICATION_RESOURCE_TYPES = frozenset({
    "match", "conversation", "profile", "reservation", "venue",
    "safe_meet", "hangout", "event", "verification", "report",
    "account",
})

_DEDUPE_KEY_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z"
)


def _object_id(value, field):
    if isinstance(value, ObjectId):
        return value

    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}.")

    try:
        return ObjectId(value.strip())
    except (InvalidId, TypeError, ValueError):
        raise ValueError(f"Invalid {field}.") from None


def _utc_timestamp(value, field):
    if not isinstance(value, datetime):
        raise ValueError(f"Invalid {field}.")

    if value.tzinfo is None:
        # PyMongo defaults to decoding UTC timestamps as naive.
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def normalize_notification_type(value):
    if not isinstance(value, str):
        raise ValueError("Invalid notification type.")

    normalized = value.strip().lower()

    if normalized not in NOTIFICATION_TYPES:
        raise ValueError("Unsupported notification type.")

    return normalized


def normalize_notification_resource(
    resource_type=None,
    resource_id=None,
):
    if resource_type is None and resource_id is None:
        return None, None

    if not isinstance(resource_type, str):
        raise ValueError("Invalid notification resource type.")

    normalized = resource_type.strip().lower()

    if normalized not in NOTIFICATION_RESOURCE_TYPES:
        raise ValueError("Unsupported notification resource type.")

    return normalized, _object_id(
        resource_id,
        "notification resource ID",
    )


def _dedupe_key(value):
    if value is None:
        return None

    if (
        not isinstance(value, str)
        or not _DEDUPE_KEY_PATTERN.fullmatch(value)
    ):
        raise ValueError("Invalid notification dedupe key.")

    return value


def build_notification_document(
    *,
    user_id,
    notification_type,
    resource_type=None,
    resource_id=None,
    dedupe_key=None,
    now=None,
):
    """Build an unread, privacy-minimal MongoDB notification record.

    Repository-level atomic idempotency will be implemented separately.
    Supplying a dedupe key here does not itself guarantee uniqueness.
    """

    recipient = _object_id(
        user_id,
        "notification recipient ID",
    )

    kind = normalize_notification_type(
        notification_type
    )

    resource_kind, target_id = normalize_notification_resource(
        resource_type,
        resource_id,
    )

    timestamp = _utc_timestamp(
        datetime.now(timezone.utc) if now is None else now,
        "notification time",
    )

    title, body = NOTIFICATION_COPY[kind]

    return {
        "schema_version": NOTIFICATION_SCHEMA_VERSION,
        "user_id": recipient,
        "type": kind,
        "title": title,
        "body": body,
        "resource_type": resource_kind,
        "resource_id": target_id,
        "dedupe_key": _dedupe_key(dedupe_key),
        "created_at": timestamp,
        "updated_at": timestamp,
        "read_at": None,
        "dismissed_at": None,
    }


def serialize_notification_for_user(
    notification,
    user_id,
):
    """Serialize an owned notification with an explicit field allowlist.

    Callers must also revalidate access to referenced resources.
    The service must suppress or redact records made unsafe by
    blocking, unmatching, deletion, or privacy changes.
    """

    if not isinstance(notification, dict):
        raise ValueError("Invalid notification record.")

    recipient = _object_id(
        user_id,
        "notification recipient ID",
    )

    owner = _object_id(
        notification.get("user_id"),
        "notification owner ID",
    )

    if owner != recipient:
        raise ValueError(
            "Notification does not belong to this user."
        )

    if notification.get("schema_version") != NOTIFICATION_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported notification schema version."
        )

    notification_id = _object_id(
        notification.get("_id"),
        "notification ID",
    )

    kind = normalize_notification_type(
        notification.get("type")
    )

    resource_kind, target_id = normalize_notification_resource(
        notification.get("resource_type"),
        notification.get("resource_id"),
    )

    created_at = _utc_timestamp(
        notification.get("created_at"),
        "created_at",
    )

    read_at = notification.get("read_at")
    dismissed_at = notification.get("dismissed_at")

    if read_at is not None:
        read_at = _utc_timestamp(read_at, "read_at")

    if dismissed_at is not None:
        dismissed_at = _utc_timestamp(
            dismissed_at,
            "dismissed_at",
        )

    title, body = NOTIFICATION_COPY[kind]

    return {
        "id": str(notification_id),
        "schema_version": NOTIFICATION_SCHEMA_VERSION,
        "type": kind,
        "title": title,
        "body": body,
        "resource_type": resource_kind,
        "resource_id": str(target_id) if target_id is not None else None,
        "created_at": created_at.isoformat(),
        "read_at": read_at.isoformat() if read_at is not None else None,
        "dismissed_at": (
            dismissed_at.isoformat()
            if dismissed_at is not None
            else None
        ),
        "is_read": read_at is not None,
    }


__all__ = [
    "NOTIFICATION_SCHEMA_VERSION",
    "NOTIFICATION_TYPES",
    "NOTIFICATION_RESOURCE_TYPES",
    "build_notification_document",
    "normalize_notification_type",
    "normalize_notification_resource",
    "serialize_notification_for_user",
]
