"""Verified-recipient policy for Xuoroni Notification emails.

No SMTP sending or delivery-state mutation happens here.
"""

from bson import ObjectId

from app.repositories.auth_repository import get_identities_for_user
from app.schemas.notification import NOTIFICATION_COPY


class NotificationEmailSuppressed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def resolve_verified_notification_email(user_id):
    if isinstance(user_id, bool):
        raise ValueError("Invalid Notification owner.")

    try:
        owner = (
            user_id
            if isinstance(user_id, ObjectId)
            else ObjectId(str(user_id))
        )
    except Exception:
        raise ValueError("Invalid Notification owner.") from None

    addresses = set()

    for identity in get_identities_for_user(owner):
        if identity.get("user_id") != owner:
            continue

        if identity.get("verified") is not True:
            continue

        raw = identity.get("email_normalized")
        if not isinstance(raw, str):
            continue

        address = raw.strip().lower()

        if (
            not address
            or len(address) > 254
            or address.count("@") != 1
            or any(character.isspace() for character in address)
            or not all(address.split("@"))
        ):
            continue

        addresses.add(address)

    if not addresses:
        raise NotificationEmailSuppressed(
            "NO_VERIFIED_EMAIL"
        )

    if len(addresses) > 1:
        raise NotificationEmailSuppressed(
            "AMBIGUOUS_VERIFIED_EMAIL"
        )

    return next(iter(addresses))


def prepare_notification_email(notification):
    if not isinstance(notification, dict):
        raise ValueError("Invalid Notification.")

    kind = notification.get("type")

    if kind not in NOTIFICATION_COPY:
        raise ValueError("Invalid Notification type.")

    recipient = resolve_verified_notification_email(
        notification.get("user_id")
    )

    title, body = NOTIFICATION_COPY[kind]

    return {
        "recipient": recipient,
        "subject": "Xuoroni: " + title,
        "text_body": (
            body
            + "\n\nOpen Xuoroni to view your notifications."
        ),
    }


__all__ = [
    "NotificationEmailSuppressed",
    "resolve_verified_notification_email",
    "prepare_notification_email",
]
