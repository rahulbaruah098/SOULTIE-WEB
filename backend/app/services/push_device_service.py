"""Validation and safe owner serialization for push devices."""

import re

from app.repositories.push_device_repository import (
    register_push_device,
    list_user_push_devices,
    revoke_user_push_device,
)

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
ALLOWED_PLATFORMS = {"android", "ios", "web"}


class InvalidPushDevice(ValueError):
    pass


def normalize_device_id(value):
    if not isinstance(value, str):
        raise InvalidPushDevice("Invalid device ID.")
    value = value.strip()
    if not DEVICE_ID_PATTERN.fullmatch(value):
        raise InvalidPushDevice("Invalid device ID.")
    return value


def _required_string(value, field, limit):
    if not isinstance(value, str):
        raise InvalidPushDevice(f"Invalid {field}.")
    value = value.strip()
    if not value or len(value) > limit:
        raise InvalidPushDevice(f"Invalid {field}.")
    return value


def serialize_push_device(document):
    return {
        "device_id": document["device_id"],
        "platform": document.get("platform"),
        "device_name": document.get("device_name"),
        "push_provider": document.get("push_provider"),
        "push_enabled": document.get("push_enabled") is True,
        "created_at": document["created_at"].isoformat(),
        "updated_at": document["updated_at"].isoformat(),
    }


def register_my_push_device(user_id, payload, *, family_id=None):
    if not isinstance(payload, dict):
        raise InvalidPushDevice("Invalid request body.")

    allowed = {
        "device_id",
        "platform",
        "push_provider",
        "push_token",
        "device_name",
    }

    if set(payload) - allowed:
        raise InvalidPushDevice("Unknown device fields.")

    device_id = normalize_device_id(payload.get("device_id"))

    platform = _required_string(
        payload.get("platform"), "platform", 20
    ).lower()
    if platform not in ALLOWED_PLATFORMS:
        raise InvalidPushDevice("Unsupported platform.")

    provider = _required_string(
        payload.get("push_provider"), "push provider", 32
    ).lower()
    if not PROVIDER_PATTERN.fullmatch(provider):
        raise InvalidPushDevice("Invalid push provider.")

    token = _required_string(
        payload.get("push_token"), "push token", 4096
    )

    if any(ord(char) < 33 or ord(char) == 127 for char in token):
        raise InvalidPushDevice("Invalid push token.")

    name = payload.get("device_name")
    if name is not None:
        name = _required_string(name, "device name", 100)

    document = register_push_device(
        user_id=user_id,
        device_id=device_id,
        platform=platform,
        push_provider=provider,
        push_token=token,
        device_name=name,
        **({"family_id": family_id} if family_id is not None else {}),
    )

    return serialize_push_device(document)


def list_my_push_devices(user_id):
    return [
        serialize_push_device(document)
        for document in list_user_push_devices(user_id)
    ]


def revoke_my_push_device(user_id, device_id):
    normalized = normalize_device_id(device_id)
    return revoke_user_push_device(
        user_id=user_id,
        device_id=normalized,
    ) is not None
