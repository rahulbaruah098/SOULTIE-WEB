"""Authenticated HTTP tests for Xuoroni matching."""

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
    "xuoroni_test_matches_api"
)


@pytest.fixture()
def app():
    otp_prefix = (
        "xuoroni:test:matches-api:"
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
                "xuoroni-matches-api-test-"
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
    phone,
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


def _activate_user(
    user_id,
    *,
    profile_status="active",
    visibility="visible",
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
                    f"Match API {user_id}"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "gender_identity": "woman",
                "profile_status": (
                    profile_status
                ),
                "visibility": (
                    visibility
                ),
                "onboarding_status": (
                    onboarding_status
                ),
                "profile_completion_percent": (
                    100
                ),
            }
        },
    )

    mongo.db.users.update_one(
        {
            "_id": user_id,
        },
        {
            "$set": {
                "onboarding_status": (
                    onboarding_status
                ),
            }
        },
    )


def _create_ready_target():
    user = create_user()

    user_id = user["_id"]

    profile = create_profile(
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
                    f"Target {user_id}"
                ),
                "birth_date": (
                    "2000-01-01"
                ),
                "gender_identity": "woman",
                "profile_status": "active",
                "visibility": "visible",
                "onboarding_status": (
                    "completed"
                ),
                "profile_completion_percent": (
                    100
                ),
            }
        },
    )

    mongo.db.users.update_one(
        {
            "_id": user_id,
        },
        {
            "$set": {
                "onboarding_status": (
                    "completed"
                ),
            }
        },
    )

    return user_id


def test_matches_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/matches"
    )

    assert response.status_code == 401


def test_actions_requires_authentication(
    client,
):
    response = client.post(
        "/api/v1/matches/actions",
        json={
            "target_user_id": (
                str(
                    ObjectId()
                )
            ),
            "action": "like",
        },
    )

    assert response.status_code == 401


def test_like_api_without_reciprocal_does_not_match(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543301",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

        target_id = (
            _create_ready_target()
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": (
                str(
                    target_id
                )
            ),
            "action": "like",
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["action"] == "like"
    assert body["matched"] is False
    assert body["match"] is None


def test_super_like_api_is_supported(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543302",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

        target_id = (
            _create_ready_target()
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": (
                str(
                    target_id
                )
            ),
            "action": "super_like",
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert (
        body["action"]
        == "super_like"
    )

    assert body["matched"] is False


def test_pass_api_does_not_create_match(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543303",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

        target_id = (
            _create_ready_target()
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": (
                str(
                    target_id
                )
            ),
            "action": "pass",
        },
    )

    assert response.status_code == 200

    assert response.get_json() == {
        "action": "pass",
        "matched": False,
        "match": None,
    }


def test_mutual_like_creates_real_match(
    client,
    app,
):
    first = _login(
        client,
        phone="9876543304",
    )

    second = _login(
        client,
        phone="9876543305",
    )

    with app.app_context():
        _activate_user(
            first["user_id"]
        )

        _activate_user(
            second["user_id"]
        )

    first_response = client.post(
        "/api/v1/matches/actions",
        headers=first["headers"],
        json={
            "target_user_id": str(
                second["user_id"]
            ),
            "action": "like",
        },
    )

    assert (
        first_response.status_code
        == 200
    )

    assert (
        first_response.get_json()[
            "matched"
        ]
        is False
    )

    second_response = client.post(
        "/api/v1/matches/actions",
        headers=second["headers"],
        json={
            "target_user_id": str(
                first["user_id"]
            ),
            "action": "super_like",
        },
    )

    assert (
        second_response.status_code
        == 200
    )

    body = (
        second_response.get_json()
    )

    assert body["matched"] is True

    assert (
        body["match"][
            "other_user_id"
        ]
        == str(
            first["user_id"]
        )
    )

    assert (
        body["match"]["status"]
        == "active"
    )

    assert body["match"]["id"]


def test_match_list_get_and_unmatch_api(
    client,
    app,
):
    first = _login(
        client,
        phone="9876543306",
    )

    second = _login(
        client,
        phone="9876543307",
    )

    with app.app_context():
        _activate_user(
            first["user_id"]
        )

        _activate_user(
            second["user_id"]
        )

    client.post(
        "/api/v1/matches/actions",
        headers=first["headers"],
        json={
            "target_user_id": str(
                second["user_id"]
            ),
            "action": "like",
        },
    )

    mutual = client.post(
        "/api/v1/matches/actions",
        headers=second["headers"],
        json={
            "target_user_id": str(
                first["user_id"]
            ),
            "action": "like",
        },
    )

    assert mutual.status_code == 200

    match_id = (
        mutual.get_json()[
            "match"
        ][
            "id"
        ]
    )

    listing = client.get(
        "/api/v1/matches",
        headers=first["headers"],
    )

    assert listing.status_code == 200

    listing_body = (
        listing.get_json()
    )

    assert (
        listing_body["returned"]
        == 1
    )

    assert (
        listing_body["matches"][0][
            "id"
        ]
        == match_id
    )

    single = client.get(
        (
            "/api/v1/matches/"
            + match_id
        ),
        headers=first["headers"],
    )

    assert single.status_code == 200

    assert (
        single.get_json()[
            "match"
        ][
            "other_user_id"
        ]
        == str(
            second["user_id"]
        )
    )

    unmatch_response = client.post(
        (
            "/api/v1/matches/"
            + match_id
            + "/unmatch"
        ),
        headers=first["headers"],
    )

    assert (
        unmatch_response.status_code
        == 200
    )

    assert (
        unmatch_response.get_json()[
            "match"
        ][
            "status"
        ]
        == "unmatched"
    )

    active = client.get(
        "/api/v1/matches",
        headers=first["headers"],
    )

    assert active.status_code == 200

    assert (
        active.get_json()[
            "returned"
        ]
        == 0
    )

    history = client.get(
        (
            "/api/v1/matches"
            "?status=unmatched"
        ),
        headers=first["headers"],
    )

    assert history.status_code == 200

    assert (
        history.get_json()[
            "returned"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "reverse_block",
    [
        False,
        True,
    ],
)
def test_blocked_pair_api_is_rejected(
    client,
    app,
    reverse_block,
):
    login = _login(
        client,
        phone=(
            "9876543310"
            if reverse_block
            else "9876543309"
        ),
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

        target_id = (
            _create_ready_target()
        )

        blocker_id = (
            target_id
            if reverse_block
            else login["user_id"]
        )

        blocked_id = (
            login["user_id"]
            if reverse_block
            else target_id
        )

        mongo.db.blocks.insert_one(
            {
                "blocker_id": (
                    blocker_id
                ),
                "blocked_id": (
                    blocked_id
                ),
            }
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": str(
                target_id
            ),
            "action": "like",
        },
    )

    assert response.status_code == 409

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "PAIR_BLOCKED"
    )


def test_action_api_rejects_missing_json_body(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543311",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
    )

    assert response.status_code == 400

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCHING_VALIDATION_ERROR"
    )


def test_action_api_rejects_invalid_target_id(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543312",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": (
                "not-an-object-id"
            ),
            "action": "like",
        },
    )

    assert response.status_code == 400

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "INVALID_USER_ID"
    )


def test_action_api_rejects_invalid_action(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543313",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

        target_id = (
            _create_ready_target()
        )

    response = client.post(
        "/api/v1/matches/actions",
        headers=login["headers"],
        json={
            "target_user_id": str(
                target_id
            ),
            "action": "block",
        },
    )

    assert response.status_code == 400

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "INVALID_PROFILE_ACTION"
    )


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=101",
        "?limit=abc",
        "?status=pending",
    ],
)
def test_match_list_api_rejects_invalid_query(
    client,
    app,
    query,
):
    login = _login(
        client,
        phone=(
            "98765433"
            + str(
                20
                + len(query)
            )
        ),
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

    response = client.get(
        (
            "/api/v1/matches"
            + query
        ),
        headers=login["headers"],
    )

    assert response.status_code == 400

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        in {
            "INVALID_LIMIT",
            "INVALID_MATCH_STATUS",
        }
    )


def test_match_get_invalid_id_returns_not_found(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543314",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

    response = client.get(
        (
            "/api/v1/matches/"
            "not-an-object-id"
        ),
        headers=login["headers"],
    )

    assert response.status_code == 404

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_NOT_FOUND"
    )


def test_active_match_rejects_pass_api(
    client,
    app,
):
    first = _login(
        client,
        phone="9876543315",
    )

    second = _login(
        client,
        phone="9876543316",
    )

    with app.app_context():
        _activate_user(
            first["user_id"]
        )

        _activate_user(
            second["user_id"]
        )

    client.post(
        "/api/v1/matches/actions",
        headers=first["headers"],
        json={
            "target_user_id": str(
                second["user_id"]
            ),
            "action": "like",
        },
    )

    matched = client.post(
        "/api/v1/matches/actions",
        headers=second["headers"],
        json={
            "target_user_id": str(
                first["user_id"]
            ),
            "action": "like",
        },
    )

    assert matched.status_code == 200

    response = client.post(
        "/api/v1/matches/actions",
        headers=first["headers"],
        json={
            "target_user_id": str(
                second["user_id"]
            ),
            "action": "pass",
        },
    )

    assert response.status_code == 409

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_ALREADY_ACTIVE"
    )


def _create_active_match_for_experience(
    client,
    app,
    *,
    first_phone,
    second_phone,
):
    first = _login(
        client,
        phone=first_phone,
    )

    second = _login(
        client,
        phone=second_phone,
    )

    with app.app_context():
        _activate_user(
            first["user_id"]
        )

        _activate_user(
            second["user_id"]
        )

    first_action = client.post(
        "/api/v1/matches/actions",
        headers=first["headers"],
        json={
            "target_user_id": str(
                second["user_id"]
            ),
            "action": "like",
        },
    )

    assert (
        first_action.status_code
        == 200
    )

    assert (
        first_action.get_json()[
            "matched"
        ]
        is False
    )

    second_action = client.post(
        "/api/v1/matches/actions",
        headers=second["headers"],
        json={
            "target_user_id": str(
                first["user_id"]
            ),
            "action": "like",
        },
    )

    assert (
        second_action.status_code
        == 200
    )

    body = (
        second_action.get_json()
    )

    assert body["matched"] is True

    return {
        "first": first,
        "second": second,
        "match_id": body[
            "match"
        ][
            "id"
        ],
    }


def test_match_experience_requires_authentication(
    client,
):
    response = client.get(
        (
            "/api/v1/matches/"
            + str(
                ObjectId()
            )
            + "/experience"
        )
    )

    assert (
        response.status_code
        == 401
    )


def test_match_experience_success(
    client,
    app,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543401",
            second_phone="9876543402",
        )
    )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    body = (
        response.get_json()
    )

    assert (
        body["version"]
        == "v1"
    )

    assert (
        body[
            "match"
        ][
            "id"
        ]
        == pair["match_id"]
    )

    assert (
        body[
            "match"
        ][
            "status"
        ]
        == "active"
    )

    assert (
        body[
            "match"
        ][
            "other_user_id"
        ]
        == str(
            pair[
                "second"
            ][
                "user_id"
            ]
        )
    )

    assert (
        body[
            "compatibility"
        ][
            "target_user_id"
        ]
        == str(
            pair[
                "second"
            ][
                "user_id"
            ]
        )
    )

    assert isinstance(
        body["icebreakers"],
        list,
    )

    assert (
        len(
            body[
                "icebreakers"
            ]
        )
        >= 1
    )

    assert body["chat"] == {
        "available": True,
        "conversation_id": None,
        "unlock_required": False,
    }

    with app.app_context():
        assert (
            mongo.db.conversations.count_documents(
                {
                    "match_id": ObjectId(
                        pair["match_id"]
                    ),
                }
            )
            == 0
        )


def test_match_experience_is_symmetric_for_both_members(
    client,
    app,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543403",
            second_phone="9876543404",
        )
    )

    first_response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    second_response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=pair[
            "second"
        ][
            "headers"
        ],
    )

    assert (
        first_response.status_code
        == 200
    )

    assert (
        second_response.status_code
        == 200
    )

    assert (
        first_response.get_json()[
            "match"
        ][
            "other_user_id"
        ]
        == str(
            pair[
                "second"
            ][
                "user_id"
            ]
        )
    )

    assert (
        second_response.get_json()[
            "match"
        ][
            "other_user_id"
        ]
        == str(
            pair[
                "first"
            ][
                "user_id"
            ]
        )
    )


def test_match_experience_valid_icebreaker_limit(
    client,
    app,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543405",
            second_phone="9876543406",
        )
    )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
            + "?icebreaker_limit=1"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        response.status_code
        == 200
    )

    assert (
        len(
            response.get_json()[
                "icebreakers"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "7",
        "abc",
    ],
)
def test_match_experience_rejects_invalid_icebreaker_limit(
    client,
    app,
    value,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543407",
            second_phone="9876543408",
        )
    )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
            + "?icebreaker_limit="
            + value
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        response.status_code
        == 400
    )

    body = (
        response.get_json()
    )

    assert (
        body[
            "error"
        ][
            "code"
        ]
        == "INVALID_ICEBREAKER_LIMIT"
    )


def test_match_experience_invalid_match_returns_not_found(
    client,
    app,
):
    login = _login(
        client,
        phone="9876543409",
    )

    with app.app_context():
        _activate_user(
            login["user_id"]
        )

    response = client.get(
        (
            "/api/v1/matches/"
            "not-an-object-id"
            "/experience"
        ),
        headers=login[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 404
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_EXPERIENCE_NOT_FOUND"
    )


def test_match_experience_non_member_cannot_access(
    client,
    app,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543410",
            second_phone="9876543411",
        )
    )

    outsider = _login(
        client,
        phone="9876543412",
    )

    with app.app_context():
        _activate_user(
            outsider["user_id"]
        )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=outsider[
            "headers"
        ],
    )

    assert (
        response.status_code
        == 404
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_EXPERIENCE_NOT_FOUND"
    )


def test_match_experience_unmatched_pair_is_rejected(
    client,
    app,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543413",
            second_phone="9876543414",
        )
    )

    unmatch = client.post(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/unmatch"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        unmatch.status_code
        == 200
    )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        response.status_code
        == 409
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_INACTIVE"
    )


@pytest.mark.parametrize(
    "reverse_block",
    [
        False,
        True,
    ],
)
def test_match_experience_blocked_pair_fails_closed(
    client,
    app,
    reverse_block,
):
    pair = (
        _create_active_match_for_experience(
            client,
            app,
            first_phone="9876543415",
            second_phone="9876543416",
        )
    )

    with app.app_context():
        blocker_id = (
            pair[
                "second"
            ][
                "user_id"
            ]
            if reverse_block
            else pair[
                "first"
            ][
                "user_id"
            ]
        )

        blocked_id = (
            pair[
                "first"
            ][
                "user_id"
            ]
            if reverse_block
            else pair[
                "second"
            ][
                "user_id"
            ]
        )

        mongo.db.blocks.insert_one(
            {
                "blocker_id": blocker_id,
                "blocked_id": blocked_id,
            }
        )

    response = client.get(
        (
            "/api/v1/matches/"
            + pair["match_id"]
            + "/experience"
        ),
        headers=pair[
            "first"
        ][
            "headers"
        ],
    )

    assert (
        response.status_code
        == 404
    )

    assert (
        response.get_json()[
            "error"
        ][
            "code"
        ]
        == "MATCH_EXPERIENCE_NOT_AVAILABLE"
    )
