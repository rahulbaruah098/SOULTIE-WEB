"""Permanent partial-fanout FCM recovery acceptance test."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

from bson import ObjectId
from flask import Flask

from app.services import notification_delivery_executor as executor


def test_accepted_device_is_not_resent_after_retry(monkeypatch):
    app = Flask(__name__)

    delivery_id = ObjectId()
    user_id = ObjectId()
    notification_id = ObjectId()

    first_token = "first-device-token-" + "a" * 30
    second_token = "second-device-token-" + "b" * 30

    devices = [
        {
            "device_id": "device-a",
            "push_token": first_token,
        },
        {
            "device_id": "device-b",
            "push_token": second_token,
        },
    ]

    state = {
        "_id": delivery_id,
        "status": "processing",
        "attempt_count": 1,
        "push_accepted_devices": [],
    }

    sent = []
    failures_remaining = [1]

    class DeliveryCollection:
        def find_one(self, query, projection=None):
            if query.get("_id") != delivery_id:
                return None
            if query.get("status") != state["status"]:
                return None
            if query.get("attempt_count") != state["attempt_count"]:
                return None
            return {"_id": delivery_id}

        def update_one(self, query, change):
            matched = self.find_one(query) is not None

            if matched:
                for key, value in change.get("$set", {}).items():
                    state[key] = value

                for key, value in change.get("$addToSet", {}).items():
                    values = state.setdefault(key, [])
                    if value not in values:
                        values.append(value)

            return SimpleNamespace(
                matched_count=1 if matched else 0
            )

    class DeviceCollection:
        def find(self, query, projection=None):
            assert query["user_id"] == user_id
            assert query["push_provider"] == "fcm"
            return self

        def limit(self, count):
            assert count == 21
            return devices

    class NotificationCollection:
        def find_one(self, query):
            assert query["user_id"] == user_id
            return {
                "_id": notification_id,
                "user_id": user_id,
                "type": "booking_update",
            }

    database = SimpleNamespace(
        notification_deliveries=DeliveryCollection(),
        user_devices=DeviceCollection(),
        notifications=NotificationCollection(),
    )

    monkeypatch.setattr(
        executor, "mongo", SimpleNamespace(db=database)
    )

    monkeypatch.setenv(
        "XUORONI_FCM_PUSH_DELIVERY_ENABLED", "true"
    )

    monkeypatch.setattr(
        executor,
        "get_notification_preferences_for_user",
        lambda user_id: {},
    )

    monkeypatch.setattr(
        executor,
        "notification_channel_is_enabled",
        lambda preferences, channel: True,
    )

    class Provider:
        def send(self, *, token, title, body, data, enabled):
            assert enabled is True
            sent.append(token)

            if token == second_token and failures_remaining[0]:
                failures_remaining[0] -= 1
                raise executor.FirebasePushError(
                    "FCM_PROVIDER_UNAVAILABLE"
                )

            return {
                "accepted": True,
                "message_name": "projects/test/messages/123",
            }

    monkeypatch.setattr(
        executor,
        "FirebasePushService",
        lambda: Provider(),
    )

    retries = []
    delivered = []

    def reschedule(delivery_id_arg, **kwargs):
        retries.append(kwargs)
        return {"status": "pending"}

    def acknowledge(delivery_id_arg, **kwargs):
        delivered.append(kwargs)
        return {"status": "delivered"}

    monkeypatch.setattr(
        executor,
        "reschedule_notification_delivery",
        reschedule,
    )

    monkeypatch.setattr(
        executor,
        "mark_notification_delivery_delivered",
        acknowledge,
    )

    def claim(attempt):
        return {
            "_id": delivery_id,
            "user_id": user_id,
            "notification_id": notification_id,
            "channel": "push",
            "attempt_count": attempt,
            "lease_expires_at": (
                datetime.now(timezone.utc)
                + timedelta(minutes=5)
            ),
            "push_accepted_devices": list(
                state["push_accepted_devices"]
            ),
        }

    current_claim = [claim(1)]

    monkeypatch.setattr(
        executor,
        "claim_next_due_notification_delivery",
        lambda **kwargs: current_claim[0],
    )

    with app.app_context():
        first = executor.execute_next_notification_delivery()

    assert first["status"] == "pending"
    assert sent == [first_token, second_token]
    assert len(retries) == 1

    fingerprint = sha256(first_token.encode()).hexdigest()
    assert state["push_accepted_devices"] == [fingerprint]

    state["attempt_count"] = 2
    current_claim[0] = claim(2)

    with app.app_context():
        second = executor.execute_next_notification_delivery()

    assert second["status"] == "delivered"
    assert sent == [
        first_token,
        second_token,
        second_token,
    ]
    assert len(delivered) == 1

    assert len(state["push_accepted_devices"]) == 2