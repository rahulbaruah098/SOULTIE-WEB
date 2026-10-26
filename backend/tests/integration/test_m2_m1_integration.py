"""Persistent M2-to-M1 authenticated compatibility regression."""

import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
from bson import ObjectId
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

from app import create_app
from app.extensions import mongo


@pytest.fixture
def m2_app():
    settings = dotenv_values(".env")
    uri = settings.get("MONGO_URI")
    assert uri and parse_uri(uri)["database"] == "soultie"

    db_name = "soultie_m2_test_" + uuid.uuid4().hex[:12]
    parts = urlsplit(uri)
    isolated_uri = urlunsplit((
        parts.scheme, parts.netloc, "/" + db_name,
        parts.query, parts.fragment
    ))

    admin = MongoClient(uri, serverSelectionTimeoutMS=8000)
    assert db_name not in admin.list_database_names()

    try:
        app = create_app({
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": isolated_uri,
            "EXPOSE_DEV_OTP": True,
            "DEV_OTP_CODE": "654321",
            "OTP_RESEND_COOLDOWN_SECONDS": 1,
            "OTP_KEY_PREFIX": (
                "soultie:test:m2:" + uuid.uuid4().hex + ":"
            ),
        })
        with app.app_context():
            assert mongo.db.name == db_name
            yield app
    finally:
        admin.drop_database(db_name)
        assert db_name not in admin.list_database_names()
        admin.close()


def _authenticate(client, phone):
    requested = client.post(
        "/api/v1/auth/request-otp", json={"phone": phone}
    )
    assert requested.status_code == 200

    verified = client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "otp": "654321"},
    )
    assert verified.status_code == 200

    body = verified.get_json()
    return (
        ObjectId(body["user"]["id"]),
        {"Authorization": "Bearer " + body["access_token"]},
    )


def test_m2_preferences_drive_m1_and_respect_revoked_consent(m2_app):
    client = m2_app.test_client()
    first, h1 = _authenticate(client, "9876543282")
    second, h2 = _authenticate(client, "9876543283")

    for user_id in (first, second):
        mongo.db.profiles.update_one(
            {"user_id": user_id},
            {"$set": {
                "onboarding_status": "completed",
                "profile_status": "active",
                "culture_preferences": {},
                "culture_compatibility_enabled": False,
            }},
            upsert=True,
        )

    conversation_id = ObjectId()
    mongo.db.conversations.insert_one({
        "_id": conversation_id,
        "member_ids": [first, second],
        "status": "active",
    })

    preferences = {
        "languages": ["Assamese", "English"],
        "festivals": ["Bihu"],
        "food_interests": ["Traditional food"],
        "music_interests": ["Folk"],
        "lifestyle": ["Outdoor"],
        "relationship_goals": ["Long-term"],
    }

    endpoint = "/api/v1/chats/culture-preferences"
    for headers in (h1, h2):
        response = client.put(
            endpoint, headers=headers,
            json={
                "culture_preferences": preferences,
                "culture_compatibility_enabled": True,
            },
        )
        assert response.status_code == 200

    m1_url = (
        f"/api/v1/chats/{conversation_id}/compatibility"
    )

    response = client.get(m1_url, headers=h1)
    assert response.status_code == 200
    score = response.get_json()["compatibility"]["compatibility_score"]
    assert isinstance(score, (int, float))
    assert 0 <= score <= 100

    response = client.put(
        endpoint, headers=h2,
        json={"culture_compatibility_enabled": False},
    )
    assert response.status_code == 200

    assert client.get(m1_url, headers=h1).status_code == 404
    assert client.get(m1_url).status_code == 401

    # Revoking consent must not delete user preferences.
    response = client.get(endpoint, headers=h2)
    assert response.status_code == 200
    assert response.get_json()["culture_preferences"] == preferences
    assert response.get_json()["culture_compatibility_enabled"] is False
