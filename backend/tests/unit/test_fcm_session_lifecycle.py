"""FCM session lifecycle safety checks; mocks prevent MongoDB writes."""
from unittest.mock import patch
from bson import ObjectId

from app.services import auth_service
from app.services import push_device_service


def test_logout_revokes_only_matched_family():
    uid = ObjectId()
    session = {"user_id": uid, "family_id": "family-A"}
    with (
        patch.object(auth_service, "get_session_by_refresh_hash", return_value=session),
        patch.object(auth_service, "revoke_session_by_refresh_hash", return_value=True),
        patch.object(auth_service, "revoke_family_push_devices", return_value=1) as revoke,
    ):
        assert auth_service.logout_refresh_token("test-refresh") is True
        revoke.assert_called_once_with(user_id=uid, family_id="family-A")


def test_idempotent_logout_does_not_revoke_other_devices():
    with (
        patch.object(auth_service, "get_session_by_refresh_hash", return_value=None),
        patch.object(auth_service, "revoke_session_by_refresh_hash", return_value=False),
        patch.object(auth_service, "revoke_family_push_devices") as revoke,
    ):
        assert auth_service.logout_refresh_token("already-revoked") is False
        revoke.assert_not_called()


def test_registration_stores_family_without_exposing_it():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with patch.object(push_device_service, "register_push_device", return_value={
        "device_id":"device-1", "platform":"android", "push_provider":"fcm",
        "push_enabled":True,"created_at":now,"updated_at":now,
    }) as register:
        response = push_device_service.register_my_push_device(ObjectId(), {
            "device_id":"device-1", "platform":"android", "push_provider":"fcm",
            "push_token":"synthetic-token",
        }, family_id="family-A")
        assert register.call_args.kwargs["family_id"] == "family-A"
        assert "family_id" not in response
        assert "push_token" not in response
