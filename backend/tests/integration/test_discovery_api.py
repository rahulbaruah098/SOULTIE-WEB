"""Authenticated HTTP tests for Soultie discovery."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from uuid import uuid4

import pytest
from bson import ObjectId

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.profile_repository import (
    create_profile,
    ensure_profile,
)
from app.repositories.user_repository import (
    create_user,
)


TEST_DATABASE = (
    "xuoroni_test_discovery_api"
)


@pytest.fixture()
def app():
    otp_prefix = (
        "xuoroni:test:discovery-api:"
        + uuid4().hex
        + ":"
    )

    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-discovery-api-test-"
                "secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "EXPOSE_DEV_OTP": True,
            "DEV_OTP_CODE": "654321",
            "OTP_TTL_SECONDS": 60,
            "OTP_RESEND_COOLDOWN_SECONDS": 1,
            "OTP_MAX_ATTEMPTS": 3,
            "OTP_REQUEST_WINDOW_SECONDS": 60,
            "OTP_MAX_REQUESTS_PER_WINDOW": 5,
            "OTP_KEY_PREFIX": otp_prefix,
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture()
def client(
    app,
):
    return app.test_client()


def _login(
    client,
    *,
    phone="9876543270",
):
    request_response = client.post(
        "/api/v1/auth/request-otp",
        json={
            "phone": phone,
        },
    )

    assert (
        request_response.status_code
        == 200
    )

    verify_response = client.post(
        "/api/v1/auth/verify-otp",
        json={
            "phone": phone,
            "otp": "654321",
        },
    )

    assert (
        verify_response.status_code
        == 200
    )

    payload = (
        verify_response.get_json()
    )

    return {
        "payload": payload,
        "user_id": ObjectId(
            payload[
                "user"
            ][
                "id"
            ]
        ),
        "headers": {
            "Authorization": (
                "Bearer "
                + payload[
                    "access_token"
                ]
            )
        },
    }


def _activate_requester(
    user_id,
    *,
    profile_status="active",
    onboarding_status="completed",
):
    profile = ensure_profile(
        user_id
    )

    mongo.db.profiles.update_one(
        {
            "_id": profile[
                "_id"
            ],
        },
        {
            "$set": {
                "display_name": (
                    "Requester"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "gender_identity": "man",
                "profile_status": (
                    profile_status
                ),
                "visibility": "visible",
                "onboarding_status": (
                    onboarding_status
                ),
                "profile_completion_percent": (
                    100
                ),
                "last_active_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            }
        },
    )


def _create_candidate(
    *,
    display_name,
    last_active_at=None,
    privacy=None,
):
    user = create_user()

    profile = create_profile(
        user["_id"]
    )

    default_privacy = {
        "show_age": True,
        "show_distance": True,
        "show_surname": False,
        "show_community": True,
        "show_hometown": True,
        "show_work": True,
        "show_education": True,
    }

    if privacy is not None:
        default_privacy.update(
            privacy
        )

    mongo.db.profiles.update_one(
        {
            "_id": profile[
                "_id"
            ],
        },
        {
            "$set": {
                "display_name": (
                    display_name
                ),
                "first_name": (
                    display_name
                ),
                "last_name": "Baruah",
                "surname_normalized": (
                    "baruah"
                ),
                "surname_searchable": True,
                "birth_date": (
                    "2000-01-01"
                ),
                "gender_identity": "woman",
                "current_city": {
                    "city": "Guwahati",
                    "state": "Assam",
                    "country": "India",
                },
                "hometown": {
                    "city": "Jorhat",
                    "state": "Assam",
                    "country": "India",
                },
                "culture": {
                    "communities": [
                        "Assamese",
                    ],
                    "tribes": [],
                    "heritage_tags": [],
                    "festivals": [
                        "Bihu",
                    ],
                    "custom_entries": [],
                },
                "languages": [
                    "Assamese",
                    "English",
                ],
                "relationship_intentions": [
                    "long_term",
                ],
                "bio": "Candidate bio",
                "interests": [
                    "music",
                ],
                "profile_status": "active",
                "visibility": "visible",
                "onboarding_status": (
                    "completed"
                ),
                "profile_completion_percent": (
                    100
                ),
                "privacy": (
                    default_privacy
                ),
                "last_active_at": (
                    last_active_at
                    or datetime.now(
                        timezone.utc
                    )
                ),
            }
        },
    )

    return (
        user,
        mongo.db.profiles.find_one(
            {
                "_id": profile[
                    "_id"
                ],
            }
        ),
    )


def test_discovery_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/discovery"
    )

    assert (
        response.status_code
        == 401
    )


def test_incomplete_profile_cannot_use_discovery(
    client,
):
    auth = _login(
        client
    )

    response = client.get(
        "/api/v1/discovery",
        headers=auth[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 409
    )

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "DISCOVERY_ONBOARDING_INCOMPLETE"
    )


def test_disabled_profile_cannot_use_discovery(
    client,
):
    auth = _login(
        client
    )

    _activate_requester(
        auth[
            "user_id"
        ],
        profile_status="disabled",
    )

    response = client.get(
        "/api/v1/discovery",
        headers=auth[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 403
    )

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "DISCOVERY_PROFILE_DISABLED"
    )


def test_discovery_returns_privacy_safe_candidate(
    client,
):
    auth = _login(
        client
    )

    _activate_requester(
        auth[
            "user_id"
        ]
    )

    candidate_user, _ = (
        _create_candidate(
            display_name=(
                "Candidate One"
            ),
            privacy={
                "show_age": False,
                "show_community": False,
                "show_hometown": False,
                "show_work": False,
                "show_education": False,
            },
        )
    )

    response = client.get(
        "/api/v1/discovery?limit=10",
        headers=auth[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    payload = response.get_json()

    assert (
        payload[
            "pagination"
        ][
            "limit"
        ]
        == 10
    )

    assert (
        payload[
            "pagination"
        ][
            "returned"
        ]
        == 1
    )

    candidate = (
        payload[
            "candidates"
        ][0]
    )

    assert (
        candidate[
            "user_id"
        ]
        == str(
            candidate_user[
                "_id"
            ]
        )
    )

    assert (
        candidate[
            "age"
        ]
        is None
    )

    assert (
        candidate[
            "hometown"
        ]
        == {}
    )

    forbidden = {
        "_id",
        "birth_date",
        "last_name",
        "surname_normalized",
        "surname_searchable",
        "location",
        "privacy",
        "profile_status",
        "visibility",
        "onboarding_status",
        "last_active_at",
        "created_at",
        "updated_at",
    }

    assert (
        forbidden.intersection(
            candidate.keys()
        )
        == set()
    )

    assert (
        candidate[
            "culture"
        ][
            "communities"
        ]
        == []
    )


@pytest.mark.parametrize(
    (
        "query_string",
        "field",
    ),
    [
        (
            {
                "limit": "0",
            },
            "limit",
        ),
        (
            {
                "limit": "51",
            },
            "limit",
        ),
        (
            {
                "limit": "abc",
            },
            "limit",
        ),
        (
            {
                "cursor": (
                    "not-a-valid-cursor"
                ),
            },
            "cursor",
        ),
    ],
)
def test_discovery_rejects_invalid_query(
    client,
    query_string,
    field,
):
    auth = _login(
        client
    )

    _activate_requester(
        auth[
            "user_id"
        ]
    )

    response = client.get(
        "/api/v1/discovery",
        query_string=query_string,
        headers=auth[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 400
    )

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "DISCOVERY_VALIDATION_ERROR"
    )

    assert (
        payload[
            "error"
        ][
            "field"
        ]
        == field
    )


def test_discovery_cursor_paginates_without_duplicates(
    client,
):
    auth = _login(
        client
    )

    _activate_requester(
        auth[
            "user_id"
        ]
    )

    base_time = datetime(
        2026,
        10,
        3,
        12,
        0,
        tzinfo=timezone.utc,
    )

    first_user, _ = (
        _create_candidate(
            display_name="First",
            last_active_at=(
                base_time
                + timedelta(
                    minutes=3
                )
            ),
        )
    )

    second_user, _ = (
        _create_candidate(
            display_name="Second",
            last_active_at=(
                base_time
                + timedelta(
                    minutes=2
                )
            ),
        )
    )

    third_user, _ = (
        _create_candidate(
            display_name="Third",
            last_active_at=(
                base_time
                + timedelta(
                    minutes=1
                )
            ),
        )
    )

    first_response = client.get(
        "/api/v1/discovery",
        query_string={
            "limit": "2",
        },
        headers=auth[
            "headers"
        ],
    )

    assert (
        first_response.status_code
        == 200
    )

    first_payload = (
        first_response.get_json()
    )

    assert (
        first_payload[
            "pagination"
        ][
            "returned"
        ]
        == 2
    )

    assert (
        first_payload[
            "pagination"
        ][
            "has_more"
        ]
        is True
    )

    next_cursor = (
        first_payload[
            "pagination"
        ][
            "next_cursor"
        ]
    )

    assert next_cursor

    second_response = client.get(
        "/api/v1/discovery",
        query_string={
            "limit": "2",
            "cursor": next_cursor,
        },
        headers=auth[
            "headers"
        ],
    )

    assert (
        second_response.status_code
        == 200
    )

    second_payload = (
        second_response.get_json()
    )

    first_ids = {
        item[
            "user_id"
        ]
        for item in first_payload[
            "candidates"
        ]
    }

    second_ids = {
        item[
            "user_id"
        ]
        for item in second_payload[
            "candidates"
        ]
    }

    expected_ids = {
        str(
            first_user[
                "_id"
            ]
        ),
        str(
            second_user[
                "_id"
            ]
        ),
        str(
            third_user[
                "_id"
            ]
        ),
    }

    assert (
        first_ids
        .isdisjoint(
            second_ids
        )
    )

    assert (
        first_ids
        | second_ids
        == expected_ids
    )

    assert (
        second_payload[
            "pagination"
        ][
            "has_more"
        ]
        is False
    )

    assert (
        second_payload[
            "pagination"
        ][
            "next_cursor"
        ]
        is None
    )
