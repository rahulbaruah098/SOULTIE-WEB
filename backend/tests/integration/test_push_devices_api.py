"""Soultie push-device API integration and ownership tests."""

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.push_device_repository import (
    register_push_device,
)


TEST_DATABASE = "xuoroni_test_push_devices_api"


@pytest.fixture()
def app():
    application = create_app({
        "TESTING": True,
        "AUTO_ENSURE_INDEXES": False,
        "SOCKETIO_MESSAGE_QUEUE": "",
        "MONGO_URI": (
            "mongodb://localhost:27017/" + TEST_DATABASE
        ),
        "JWT_SECRET_KEY": (
            "xuoroni-push-devices-test-secret-at-least-32-bytes"
        ),
        "JWT_ISSUER": "xuoroni-test",
        "JWT_AUDIENCE": "xuoroni-test-client",
    })

    with application.app_context():
        mongo.cx.drop_database(TEST_DATABASE)
        ensure_indexes(mongo.db)
        yield application
        mongo.cx.drop_database(TEST_DATABASE)


def test_push_indexes_exist(app):
    with app.app_context():
        info = mongo.db.user_devices.index_information()
        assert "user_devices_active_push_token_unique" in info
        index = info["user_devices_active_push_token_unique"]
        assert index["unique"] is True
        assert index["partialFilterExpression"] == {
            "push_token_hash": {"$type": "string"}
        }


def test_repository_registration_rotation_and_revocation(app):
    from app.repositories.push_device_repository import (
        list_user_push_devices,
        revoke_user_push_device,
    )
    with app.app_context():
        owner = ObjectId()
        other = ObjectId()

        first = register_push_device(
            user_id=owner,
            device_id="device-1",
            platform="android",
            push_provider="fcm",
            push_token="token-one",
        )
        assert first["push_token"] == "token-one"

        repeated = register_push_device(
            user_id=owner,
            device_id="device-1",
            platform="android",
            push_provider="fcm",
            push_token="token-one",
        )
        assert repeated["_id"] == first["_id"]
        assert mongo.db.user_devices.count_documents({
            "user_id": owner
        }) == 1

        with pytest.raises(Exception) as conflict:
            register_push_device(
                user_id=other,
                device_id="other-device",
                platform="android",
                push_provider="fcm",
                push_token="token-one",
            )
        assert type(conflict.value).__name__ == "PushTokenInUse"

        rotated = register_push_device(
            user_id=owner,
            device_id="device-1",
            platform="android",
            push_provider="fcm",
            push_token="token-two",
        )
        assert rotated["_id"] == first["_id"]
        assert rotated["push_token"] == "token-two"
        assert len(list_user_push_devices(owner)) == 1

        assert revoke_user_push_device(
            user_id=other,
            device_id="device-1",
        ) is None

        revoked = revoke_user_push_device(
            user_id=owner,
            device_id="device-1",
        )
        assert revoked["push_enabled"] is False
        assert "push_token" not in revoked
        assert "push_token_hash" not in revoked
        assert list_user_push_devices(owner) == []

        revived = register_push_device(
            user_id=owner,
            device_id="device-1",
            platform="ios",
            push_provider="apns",
            push_token="token-three",
        )
        assert revived["_id"] == first["_id"]
        assert revived["revoked_at"] is None


@pytest.mark.parametrize("payload", [
    {},
    {"device_id": "a"},
    {
        "device_id": "a",
        "platform": "android",
        "push_provider": "fcm",
        "push_token": "",
    },
    {
        "device_id": "a",
        "platform": "android",
        "push_provider": "fcm",
        "push_token": "valid",
        "unexpected": True,
    },
    {
        "device_id": "a",
        "platform": "android",
        "push_provider": "fcm",
        "push_token": 123,
    },
    {
        "device_id": "../invalid",
        "platform": "android",
        "push_provider": "fcm",
        "push_token": "valid",
    },
])
def test_invalid_payload_fails_closed(payload):
    from app.services.push_device_service import (
        InvalidPushDevice,
        register_my_push_device,
    )
    with pytest.raises(InvalidPushDevice):
        register_my_push_device(ObjectId(), payload)


def test_api_requires_authentication(app):
    client = app.test_client()
    assert client.get("/api/v1/push-devices").status_code == 401
    assert client.post(
        "/api/v1/push-devices",
        json={"device_id": "x"},
    ).status_code == 401
    assert client.delete(
        "/api/v1/push-devices/x"
    ).status_code == 401


def test_device_serializer_does_not_expose_token(app):
    from app.services.push_device_service import (
        register_my_push_device,
        list_my_push_devices,
        revoke_my_push_device,
    )

    with app.app_context():
        owner = ObjectId()
        device = register_my_push_device(owner, {
            "device_id": "phone-01",
            "platform": "android",
            "push_provider": "fcm",
            "push_token": "sensitive-token-value",
            "device_name": "My phone",
        })

        assert device["device_id"] == "phone-01"
        assert "push_token" not in device
        assert "push_token_hash" not in device

        listing = list_my_push_devices(owner)
        assert len(listing) == 1
        assert "push_token" not in listing[0]
        assert "push_token_hash" not in listing[0]

        assert revoke_my_push_device(owner, "phone-01")
        assert list_my_push_devices(owner) == []

def test_authenticated_push_device_lifecycle_and_owner_isolation(
    app, monkeypatch
):
    from app.security import authentication as security

    owners = {
        "user-a": ObjectId(),
        "user-b": ObjectId(),
    }

    def authenticate(token):
        if token not in owners:
            from app.security.tokens import InvalidAccessToken
            raise InvalidAccessToken("Invalid token.")
        return {
            "payload": {
                "sub": str(owners[token]),
                "sid": "test-session",
            },
            "session": {
                "user_id": owners[token],
                "revoked_at": None,
            },
            "user": {
                "_id": owners[token],
                "account_status": "active",
            },
        }

    monkeypatch.setattr(
        security, "authenticate_access_token", authenticate
    )

    client = app.test_client()

    def headers(key):
        return {"Authorization": "Bearer " + key}

    url = "/api/v1/push-devices"
    payload = {
        "device_id": "device-A",
        "platform": "android",
        "push_provider": "fcm",
        "push_token": "private-token-A",
    }

    created = client.post(
        url, json=payload, headers=headers("user-a")
    )
    assert created.status_code == 200
    assert created.get_json()["device"]["device_id"] == "device-A"
    assert "private-token-A" not in created.get_data(as_text=True)
    assert "push_token_hash" not in created.get_data(as_text=True)

    repeated = client.post(
        url, json=payload, headers=headers("user-a")
    )
    assert repeated.status_code == 200

    listing = client.get(url, headers=headers("user-a"))
    assert listing.status_code == 200
    assert len(listing.get_json()["devices"]) == 1
    assert "private-token-A" not in listing.get_data(as_text=True)

    other_listing = client.get(
        url, headers=headers("user-b")
    )
    assert other_listing.status_code == 200
    assert other_listing.get_json()["devices"] == []

    stolen_token = client.post(
        url,
        json={
            **payload,
            "device_id": "device-B",
        },
        headers=headers("user-b"),
    )
    assert stolen_token.status_code == 409

    other_revoke = client.delete(
        url + "/device-A", headers=headers("user-b")
    )
    assert other_revoke.status_code == 404

    owned = client.get(url, headers=headers("user-a"))
    assert len(owned.get_json()["devices"]) == 1

    revoked = client.delete(
        url + "/device-A", headers=headers("user-a")
    )
    assert revoked.status_code == 200
    assert revoked.get_json()["revoked"] is True

    empty = client.get(url, headers=headers("user-a"))
    assert empty.get_json()["devices"] == []

    reclaimed = client.post(
        url,
        json={
            **payload,
            "device_id": "device-B",
        },
        headers=headers("user-b"),
    )
    assert reclaimed.status_code == 200


def test_same_owner_token_cannot_be_registered_on_two_devices(app):
    from app.repositories.push_device_repository import PushTokenInUse

    with app.app_context():
        owner = ObjectId()

        register_push_device(
            user_id=owner,
            device_id="phone-one",
            platform="android",
            push_provider="fcm",
            push_token="shared-token",
        )

        with pytest.raises(PushTokenInUse):
            register_push_device(
                user_id=owner,
                device_id="phone-two",
                platform="android",
                push_provider="fcm",
                push_token="shared-token",
            )

        assert mongo.db.user_devices.count_documents({
            "user_id": owner,
            "push_enabled": True,
        }) == 1


def test_concurrent_token_registration_has_one_winner(app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.repositories.push_device_repository import PushTokenInUse

    owners = (ObjectId(), ObjectId())
    barrier = Barrier(2, timeout=10)

    def competing_register(index):
        with app.app_context():
            barrier.wait()
            try:
                document = register_push_device(
                    user_id=owners[index],
                    device_id="competing-device-" + str(index),
                    platform="android",
                    push_provider="fcm",
                    push_token="concurrent-token",
                )
                return ("created", str(document["user_id"]))
            except PushTokenInUse:
                return ("conflict", str(owners[index]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(competing_register, index)
            for index in range(2)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(result[0] for result in results) == [
        "conflict",
        "created",
    ]

    with app.app_context():
        assert mongo.db.user_devices.count_documents({
            "push_token_hash": {
                "$type": "string",
            },
            "push_enabled": True,
        }) == 1


def test_token_rotation_releases_old_token_for_other_owner(app):
    from app.repositories.push_device_repository import (
        list_user_push_devices,
    )

    with app.app_context():
        first_owner = ObjectId()
        next_owner = ObjectId()

        register_push_device(
            user_id=first_owner,
            device_id="rotating-device",
            platform="android",
            push_provider="fcm",
            push_token="original-token",
        )

        register_push_device(
            user_id=first_owner,
            device_id="rotating-device",
            platform="android",
            push_provider="fcm",
            push_token="replacement-token",
        )

        claimed = register_push_device(
            user_id=next_owner,
            device_id="another-device",
            platform="ios",
            push_provider="apns",
            push_token="original-token",
        )

        assert claimed["user_id"] == next_owner
        assert len(list_user_push_devices(first_owner)) == 1
        assert len(list_user_push_devices(next_owner)) == 1
