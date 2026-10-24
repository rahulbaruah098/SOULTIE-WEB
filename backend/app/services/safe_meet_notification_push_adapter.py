"""Safe Meet push adapter using Xuoroni's device registry.

The actual FCM transport is injected separately. No secrets, device
tokens or private Safe Meet details enter notification payloads.
"""

from bson import ObjectId

from app.repositories.push_device_repository import (
    list_user_push_devices,
)


class SafeMeetPushAdapterError(RuntimeError):
    pass


TITLES = {
    "safe_meet_proposed": "New meetup proposal",
    "safe_meet_consent_updated": "Meetup proposal updated",
    "safe_meet_declined": "Meetup proposal declined",
    "safe_meet_cancelled": "Meetup cancelled",
    "safe_meet_completion_updated": "Meetup status updated",
}


def deliver_safe_meet_push(item, *, transport, device_loader=None):
    """Return True only when every eligible device is accepted.

    transport(token, payload) must return True after provider acceptance.
    Retries may resend to devices that succeeded in a partial batch.
    """
    if not callable(transport):
        raise ValueError("A push transport is required.")

    if not isinstance(item, dict):
        raise SafeMeetPushAdapterError("Invalid outbox notification.")

    event_type = item.get("event_type")
    recipient = item.get("recipient_id")
    meet_id = item.get("safe_meet_id")
    conversation_id = item.get("conversation_id")
    key = item.get("idempotency_key")

    if (
        event_type not in TITLES
        or any(
            type(value) is not str or not ObjectId.is_valid(value)
            for value in (recipient, meet_id, conversation_id)
        )
        or type(key) is not str
        or not key.strip()
    ):
        raise SafeMeetPushAdapterError(
            "Notification identifiers or event type are invalid."
        )

    loader = device_loader or list_user_push_devices
    devices = loader(recipient)
    if not isinstance(devices, list):
        raise SafeMeetPushAdapterError(
            "Device registry returned an unexpected result."
        )

    payload = {
        "title": TITLES[event_type],
        "body": "Open Xuoroni to view the latest update.",
        "data": {
            "type": event_type,
            "safe_meet_id": meet_id,
            "conversation_id": conversation_id,
            "event_id": key,
        },
    }

    tokens = []
    seen = set()

    for device in devices:
        if not isinstance(device, dict):
            raise SafeMeetPushAdapterError(
                "Invalid device registry entry."
            )

        if device.get("push_provider") != "fcm":
            continue
        if device.get("is_active") is False:
            continue
        if device.get("revoked_at") is not None:
            continue
        if device.get("status") in ("revoked", "disabled"):
            continue

        token = device.get("push_token")
        if not isinstance(token, str) or not token.strip():
            continue
        if token in seen:
            continue

        seen.add(token)
        tokens.append(token)

    if not tokens:
        raise SafeMeetPushAdapterError(
            "No active FCM devices registered."
        )

    all_accepted = True
    for token in tokens:
        try:
            accepted = transport(token, payload)
        except Exception:
            accepted = False
        if accepted is not True:
            all_accepted = False

    return all_accepted
