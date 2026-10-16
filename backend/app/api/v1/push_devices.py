"""Authenticated Xuoroni push-device lifecycle endpoints."""

from flask import Blueprint, g, jsonify, request

from app.errors import error_response
from app.repositories.push_device_repository import PushTokenInUse
from app.security.authentication import auth_required
from app.services.push_device_service import (
    InvalidPushDevice,
    register_my_push_device,
    list_my_push_devices,
    revoke_my_push_device,
)

push_devices_bp = Blueprint("push_devices_v1", __name__)


@push_devices_bp.post("/push-devices")
@auth_required
def register_device():
    payload = request.get_json(silent=True)

    try:
        device = register_my_push_device(
            g.current_user["_id"], payload
        )
    except InvalidPushDevice as exc:
        return error_response(
            "INVALID_PUSH_DEVICE", str(exc), 400
        )
    except PushTokenInUse:
        return error_response(
            "PUSH_TOKEN_CONFLICT",
            "This push token is already registered.",
            409,
        )

    return jsonify({"device": device}), 200


@push_devices_bp.get("/push-devices")
@auth_required
def list_devices():
    return jsonify({
        "devices": list_my_push_devices(
            g.current_user["_id"]
        )
    }), 200


@push_devices_bp.delete("/push-devices/<device_id>")
@auth_required
def revoke_device(device_id):
    try:
        revoked = revoke_my_push_device(
            g.current_user["_id"], device_id
        )
    except InvalidPushDevice as exc:
        return error_response(
            "INVALID_PUSH_DEVICE", str(exc), 400
        )

    # Do not expose whether another user owns the identifier.
    if not revoked:
        return error_response(
            "PUSH_DEVICE_NOT_FOUND",
            "Device not found.",
            404,
        )

    return jsonify({"revoked": True}), 200
