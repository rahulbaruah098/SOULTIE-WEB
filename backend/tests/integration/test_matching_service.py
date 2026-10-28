"""Integration tests for Soultie matching business rules."""

import pytest
from bson import ObjectId

from app import create_app
from app.extensions import mongo
from app.repositories.match_repository import (
    create_match_if_absent,
    get_match_between,
    get_profile_action,
    upsert_profile_action,
)
from app.repositories.profile_repository import (
    create_profile,
)
from app.repositories.user_repository import (
    create_user,
)
from app.services import matching_service
from app.services.matching_service import (
    MatchingConflictError,
    MatchingLifecycleError,
    MatchingNotFoundError,
    MatchingValidationError,
    get_my_match,
    list_my_matches,
    submit_profile_action,
    unmatch_my_match,
)


@pytest.fixture(scope="module")
def app():
    application = create_app()

    with application.app_context():
        yield application


@pytest.fixture
def matching_users(app):
    created_user_ids = []

    def create_ready_user(
        *,
        account_status="active",
        profile_status="active",
        visibility="visible",
        onboarding_status="completed",
        display_name=None,
    ):
        user = create_user()

        user_id = user["_id"]

        created_user_ids.append(
            user_id
        )

        profile = create_profile(
            user_id
        )

        mongo.db.users.update_one(
            {
                "_id": user_id,
            },
            {
                "$set": {
                    "account_status": (
                        account_status
                    ),
                }
            },
        )

        mongo.db.profiles.update_one(
            {
                "_id": profile["_id"],
            },
            {
                "$set": {
                    "profile_status": (
                        profile_status
                    ),
                    "visibility": (
                        visibility
                    ),
                    "onboarding_status": (
                        onboarding_status
                    ),
                    "display_name": (
                        display_name
                        or f"Matching Test {user_id}"
                    ),
                }
            },
        )

        return user_id

    yield create_ready_user

    if created_user_ids:
        mongo.db.notifications.delete_many(
            {
                "user_id": {
                    "$in": created_user_ids,
                }
            }
        )

        mongo.db.notification_preferences.delete_many(
            {
                "user_id": {
                    "$in": created_user_ids,
                }
            }
        )

        mongo.db.profile_actions.delete_many(
            {
                "$or": [
                    {
                        "actor_id": {
                            "$in": created_user_ids,
                        }
                    },
                    {
                        "target_id": {
                            "$in": created_user_ids,
                        }
                    },
                ]
            }
        )

        mongo.db.matches.delete_many(
            {
                "user_ids": {
                    "$in": created_user_ids,
                }
            }
        )

        mongo.db.blocks.delete_many(
            {
                "$or": [
                    {
                        "blocker_id": {
                            "$in": created_user_ids,
                        }
                    },
                    {
                        "blocked_id": {
                            "$in": created_user_ids,
                        }
                    },
                ]
            }
        )

        mongo.db.discovery_preferences.delete_many(
            {
                "user_id": {
                    "$in": created_user_ids,
                }
            }
        )

        mongo.db.profiles.delete_many(
            {
                "user_id": {
                    "$in": created_user_ids,
                }
            }
        )

        mongo.db.users.delete_many(
            {
                "_id": {
                    "$in": created_user_ids,
                }
            }
        )


def test_like_without_reciprocal_does_not_match(
    app,
    matching_users,
):
    actor = matching_users()
    target = matching_users()

    with app.app_context():
        result = submit_profile_action(
            actor_id=actor,
            target_id=target,
            action="like",
        )

        assert result["action"] == "like"
        assert result["matched"] is False
        assert result["match"] is None

        stored = get_profile_action(
            actor,
            target,
        )

        assert stored is not None
        assert stored["action"] == "like"

        assert (
            get_match_between(
                actor,
                target,
            )
            is None
        )


def test_mutual_like_creates_match(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()

    with app.app_context():
        first_result = submit_profile_action(
            actor_id=first,
            target_id=second,
            action="like",
        )

        assert (
            first_result["matched"]
            is False
        )

        second_result = submit_profile_action(
            actor_id=second,
            target_id=first,
            action="like",
        )

        assert (
            second_result["matched"]
            is True
        )

        assert (
            second_result["match"]
            is not None
        )

        assert (
            second_result[
                "match"
            ][
                "other_user_id"
            ]
            == str(first)
        )

        stored_match = get_match_between(
            first,
            second,
        )

        assert stored_match is not None
        assert (
            stored_match["status"]
            == "active"
        )


def test_super_like_and_like_create_match(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()

    with app.app_context():
        first_result = submit_profile_action(
            actor_id=first,
            target_id=second,
            action="super_like",
        )

        assert (
            first_result["matched"]
            is False
        )

        second_result = submit_profile_action(
            actor_id=second,
            target_id=first,
            action="like",
        )

        assert (
            second_result["matched"]
            is True
        )

        assert (
            get_match_between(
                first,
                second,
            )
            is not None
        )


def test_pass_never_creates_match(
    app,
    matching_users,
):
    actor = matching_users()
    target = matching_users()

    with app.app_context():
        result = submit_profile_action(
            actor_id=actor,
            target_id=target,
            action="pass",
        )

        assert result == {
            "action": "pass",
            "matched": False,
            "match": None,
        }

        assert (
            get_match_between(
                actor,
                target,
            )
            is None
        )


@pytest.mark.parametrize(
    "reverse_block",
    [
        False,
        True,
    ],
)
def test_blocked_pair_cannot_interact(
    app,
    matching_users,
    reverse_block,
):
    actor = matching_users()
    target = matching_users()

    blocker = (
        target
        if reverse_block
        else actor
    )

    blocked = (
        actor
        if reverse_block
        else target
    )

    with app.app_context():
        mongo.db.blocks.insert_one(
            {
                "blocker_id": blocker,
                "blocked_id": blocked,
            }
        )

        with pytest.raises(
            MatchingConflictError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=target,
                action="like",
            )

        assert (
            exc_info.value.code
            == "PAIR_BLOCKED"
        )


def test_self_action_is_rejected(
    app,
    matching_users,
):
    user_id = matching_users()

    with app.app_context():
        with pytest.raises(
            MatchingValidationError
        ) as exc_info:
            submit_profile_action(
                actor_id=user_id,
                target_id=user_id,
                action="like",
            )

        assert (
            exc_info.value.code
            == "SELF_ACTION_NOT_ALLOWED"
        )


def test_inactive_target_account_is_rejected(
    app,
    matching_users,
):
    actor = matching_users()

    target = matching_users(
        account_status="disabled"
    )

    with app.app_context():
        with pytest.raises(
            MatchingLifecycleError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=target,
                action="like",
            )

        assert (
            exc_info.value.code
            == "USER_ACCOUNT_INACTIVE"
        )


def test_hidden_target_profile_is_rejected(
    app,
    matching_users,
):
    actor = matching_users()

    target = matching_users(
        visibility="hidden"
    )

    with app.app_context():
        with pytest.raises(
            MatchingLifecycleError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=target,
                action="like",
            )

        assert (
            exc_info.value.code
            == "PROFILE_NOT_VISIBLE"
        )


def test_incomplete_profile_is_rejected(
    app,
    matching_users,
):
    actor = matching_users()

    target = matching_users(
        onboarding_status="in_progress"
    )

    with app.app_context():
        with pytest.raises(
            MatchingLifecycleError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=target,
                action="like",
            )

        assert (
            exc_info.value.code
            == "ONBOARDING_INCOMPLETE"
        )


def test_active_match_rejects_pass(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()

    with app.app_context():
        create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=first,
        )

        with pytest.raises(
            MatchingConflictError
        ) as exc_info:
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="pass",
            )

        assert (
            exc_info.value.code
            == "MATCH_ALREADY_ACTIVE"
        )


def test_positive_action_on_existing_match_is_idempotent(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        result = submit_profile_action(
            actor_id=first,
            target_id=second,
            action="super_like",
        )

        assert result["matched"] is True

        assert (
            result["match"]["id"]
            == str(
                created["match"]["_id"]
            )
        )

        count = mongo.db.matches.count_documents(
            {
                "user_ids": first,
            }
        )

        assert count == 1

def test_match_summary_includes_other_user_name(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users(
        display_name="Atlanta",
    )
    second = matching_users(
        display_name="Joaquin",
    )

    monkeypatch.setattr(
        matching_service,
        "get_primary_media",
        lambda user_id: None,
    )

    with app.app_context():
        create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        result = list_my_matches(
            first,
        )

        assert result["returned"] == 1

        match = result["matches"][0]

        assert (
            match["other_user_id"]
            == str(second)
        )

        assert (
            match["other_user"]["id"]
            == str(second)
        )

        assert (
            match["other_user"]["display_name"]
            == "Joaquin"
        )

        assert (
            match["other_user"]["primary_media"]
            is None
        )

def test_match_summary_is_symmetric_for_members(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users(
        display_name="Atlanta",
    )
    second = matching_users(
        display_name="Joaquin",
    )

    monkeypatch.setattr(
        matching_service,
        "get_primary_media",
        lambda user_id: None,
    )

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        match_id = created["match"]["_id"]

        first_view = get_my_match(
            user_id=first,
            match_id=match_id,
        )

        second_view = get_my_match(
            user_id=second,
            match_id=match_id,
        )

        assert (
            first_view["other_user"]["id"]
            == str(second)
        )

        assert (
            first_view["other_user"]["display_name"]
            == "Joaquin"
        )

        assert (
            second_view["other_user"]["id"]
            == str(first)
        )

        assert (
            second_view["other_user"]["display_name"]
            == "Atlanta"
        )

def test_match_summary_includes_primary_media(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users(
        display_name="Atlanta",
    )
    second = matching_users(
        display_name="Joaquin",
    )

    media_id = ObjectId()

    fake_media = {
        "_id": media_id,
        "user_id": second,
        "kind": "profile_photo",
        "status": "active",
        "position": 0,
        "is_primary": True,
    }

    monkeypatch.setattr(
        matching_service,
        "get_primary_media",
        lambda user_id: (
            fake_media
            if str(user_id) == str(second)
            else None
        ),
    )

    monkeypatch.setattr(
        matching_service,
        "serialize_profile_media",
        lambda media: {
            "id": str(media["_id"]),
            "kind": "profile_photo",
            "position": 0,
            "is_primary": True,
            "status": "active",
            "mime_type": "image/webp",
            "width": 1080,
            "height": 1350,
            "content_url": (
                f"/api/v1/media/"
                f"{media['_id']}/content"
            ),
            "thumbnail_url": None,
        },
    )

    with app.app_context():
        create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        result = list_my_matches(first)

        other_user = (
            result["matches"][0]["other_user"]
        )

        assert (
            other_user["display_name"]
            == "Joaquin"
        )

        assert (
            other_user["primary_media"]["id"]
            == str(media_id)
        )

        assert (
            other_user[
                "primary_media"
            ]["is_primary"]
            is True
        )

        assert (
            other_user[
                "primary_media"
            ]["content_url"]
            == (
                f"/api/v1/media/"
                f"{media_id}/content"
            )
        )

def test_match_other_user_summary_does_not_leak_private_fields(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users(
        display_name="Joaquin",
    )

    monkeypatch.setattr(
        matching_service,
        "get_primary_media",
        lambda user_id: None,
    )

    with app.app_context():
        create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        result = list_my_matches(first)

        other_user = (
            result["matches"][0]["other_user"]
        )

        forbidden = {
            "phone",
            "phone_number",
            "email",
            "date_of_birth",
            "dob",
            "storage_key",
            "storage_path",
        }

        assert forbidden.isdisjoint(
            other_user.keys()
        )

def test_list_get_and_unmatch_flow(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        match_id = (
            created["match"]["_id"]
        )

        listing = list_my_matches(
            first,
            status="active",
            limit=20,
        )

        assert listing["returned"] == 1

        assert (
            listing[
                "matches"
            ][0][
                "id"
            ]
            == str(match_id)
        )

        single = get_my_match(
            user_id=first,
            match_id=match_id,
        )

        assert (
            single["other_user_id"]
            == str(second)
        )

        unmatched = unmatch_my_match(
            user_id=first,
            match_id=match_id,
        )

        assert (
            unmatched["status"]
            == "unmatched"
        )

        active_listing = (
            list_my_matches(
                first,
                status="active",
            )
        )

        assert (
            active_listing["returned"]
            == 0
        )

        old_listing = list_my_matches(
            first,
            status="unmatched",
        )

        assert (
            old_listing["returned"]
            == 1
        )


def test_non_member_cannot_get_match(
    app,
    matching_users,
):
    first = matching_users()
    second = matching_users()
    outsider = matching_users()

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=first,
        )

        with pytest.raises(
            MatchingNotFoundError
        ) as exc_info:
            get_my_match(
                user_id=outsider,
                match_id=(
                    created[
                        "match"
                    ][
                        "_id"
                    ]
                ),
            )

        assert (
            exc_info.value.code
            == "MATCH_NOT_FOUND"
        )


@pytest.mark.parametrize(
    "status",
    [
        "",
        "pending",
        "deleted",
        None,
    ],
)
def test_invalid_match_status_is_rejected(
    app,
    matching_users,
    status,
):
    user_id = matching_users()

    with app.app_context():
        with pytest.raises(
            MatchingValidationError
        ) as exc_info:
            list_my_matches(
                user_id,
                status=status,
            )

        assert (
            exc_info.value.code
            == "INVALID_MATCH_STATUS"
        )


@pytest.mark.parametrize(
    "limit",
    [
        0,
        101,
        "abc",
    ],
)
def test_invalid_match_limit_is_rejected(
    app,
    matching_users,
    limit,
):
    user_id = matching_users()

    with app.app_context():
        with pytest.raises(
            MatchingValidationError
        ) as exc_info:
            list_my_matches(
                user_id,
                limit=limit,
            )

        assert (
            exc_info.value.code
            == "INVALID_LIMIT"
        )


def test_invalid_profile_action_is_rejected(
    app,
    matching_users,
):
    actor = matching_users()
    target = matching_users()

    with app.app_context():
        with pytest.raises(
            MatchingValidationError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=target,
                action="block",
            )

        assert (
            exc_info.value.code
            == "INVALID_PROFILE_ACTION"
        )


def test_missing_target_user_is_rejected(
    app,
    matching_users,
):
    actor = matching_users()
    missing = ObjectId()

    with app.app_context():
        with pytest.raises(
            MatchingNotFoundError
        ) as exc_info:
            submit_profile_action(
                actor_id=actor,
                target_id=missing,
                action="like",
            )

        assert (
            exc_info.value.code
            == "USER_NOT_FOUND"
        )


def test_new_mutual_match_emits_match_created_to_both_members(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    emitted = []

    def fake_emit_match_created(
        *,
        user_id,
        match,
    ):
        emitted.append(
            {
                "user_id": str(
                    user_id
                ),
                "match": dict(
                    match
                ),
            }
        )

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        fake_emit_match_created,
    )

    with app.app_context():
        first_action = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            first_action["matched"]
            is False
        )

        assert emitted == []

        second_action = (
            submit_profile_action(
                actor_id=second,
                target_id=first,
                action="like",
            )
        )

        assert (
            second_action["matched"]
            is True
        )

        assert len(
            emitted
        ) == 2

        assert {
            item["user_id"]
            for item in emitted
        } == {
            str(first),
            str(second),
        }

        first_event = next(
            item
            for item in emitted
            if (
                item["user_id"]
                == str(first)
            )
        )

        second_event = next(
            item
            for item in emitted
            if (
                item["user_id"]
                == str(second)
            )
        )

        assert (
            first_event[
                "match"
            ][
                "other_user_id"
            ]
            == str(second)
        )

        assert (
            second_event[
                "match"
            ][
                "other_user_id"
            ]
            == str(first)
        )

        assert (
            first_event[
                "match"
            ][
                "id"
            ]
            == second_event[
                "match"
            ][
                "id"
            ]
        )

        stored = get_match_between(
            first,
            second,
        )

        assert stored is not None

        assert (
            stored["status"]
            == "active"
        )


def test_existing_active_match_does_not_reemit_match_created(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    emitted = []

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        lambda **kwargs: (
            emitted.append(
                kwargs
            )
        ),
    )

    with app.app_context():
        first_action = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            first_action["matched"]
            is False
        )

        created = (
            submit_profile_action(
                actor_id=second,
                target_id=first,
                action="like",
            )
        )

        assert (
            created["matched"]
            is True
        )

        assert len(
            emitted
        ) == 2

        emitted.clear()

        repeated = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="super_like",
            )
        )

        assert (
            repeated["matched"]
            is True
        )

        assert emitted == []

        assert (
            repeated[
                "match"
            ][
                "id"
            ]
            == created[
                "match"
            ][
                "id"
            ]
        )

        assert (
            mongo.db.matches.count_documents(
                {
                    "_id": (
                        get_match_between(
                            first,
                            second,
                        )["_id"]
                    ),
                }
            )
            == 1
        )


def test_repository_created_false_does_not_emit_match_created(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    emitted = []

    with app.app_context():
        existing = (
            create_match_if_absent(
                first_user_id=first,
                second_user_id=second,
                matched_by=first,
            )
        )

        raw_match = existing[
            "match"
        ]

        upsert_profile_action(
            actor_id=second,
            target_id=first,
            action="like",
        )

        monkeypatch.setattr(
            matching_service,
            "get_match_between",
            lambda first_user_id, second_user_id: None,
        )

        monkeypatch.setattr(
            matching_service,
            "create_match_if_absent",
            lambda **kwargs: {
                "match": raw_match,
                "created": False,
            },
        )

        monkeypatch.setattr(
            matching_service,
            "emit_match_created",
            lambda **kwargs: (
                emitted.append(
                    kwargs
                )
            ),
        )

        result = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            result["matched"]
            is True
        )

        assert emitted == []


def test_realtime_failure_does_not_fail_durable_match(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    attempted = []

    def failing_emit(
        *,
        user_id,
        match,
    ):
        attempted.append(
            str(
                user_id
            )
        )

        raise RuntimeError(
            "Simulated realtime failure."
        )

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        failing_emit,
    )

    with app.app_context():
        first_action = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            first_action["matched"]
            is False
        )

        result = (
            submit_profile_action(
                actor_id=second,
                target_id=first,
                action="like",
            )
        )

        assert (
            result["matched"]
            is True
        )

        assert attempted == [
            str(second),
            str(first),
        ]

        stored = get_match_between(
            first,
            second,
        )

        assert stored is not None

        assert (
            stored["status"]
            == "active"
        )

        assert (
            str(
                stored["_id"]
            )
            == result[
                "match"
            ][
                "id"
            ]
        )

        assert (
            mongo.db.matches.count_documents(
                {
                    "_id": stored[
                        "_id"
                    ],
                }
            )
            == 1
        )


def test_new_mutual_match_creates_durable_notifications_for_both_members(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    emitted = []

    def fake_emit_match_created(
        *,
        user_id,
        match,
    ):
        emitted.append(
            str(
                user_id
            )
        )

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        fake_emit_match_created,
    )

    with app.app_context():
        first_action = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            first_action[
                "matched"
            ]
            is False
        )

        assert (
            mongo.db.notifications.count_documents(
                {
                    "user_id": {
                        "$in": [
                            first,
                            second,
                        ],
                    }
                }
            )
            == 0
        )

        result = (
            submit_profile_action(
                actor_id=second,
                target_id=first,
                action="like",
            )
        )

        assert (
            result[
                "matched"
            ]
            is True
        )

        match = get_match_between(
            first,
            second,
        )

        assert match is not None

        notifications = list(
            mongo.db.notifications.find(
                {
                    "user_id": {
                        "$in": [
                            first,
                            second,
                        ],
                    },
                    "type": "new_match",
                    "resource_type": "match",
                    "resource_id": (
                        match[
                            "_id"
                        ]
                    ),
                }
            )
        )

        assert (
            len(
                notifications
            )
            == 2
        )

        assert {
            item[
                "user_id"
            ]
            for item in notifications
        } == {
            first,
            second,
        }

        assert {
            item[
                "dedupe_key"
            ]
            for item in notifications
        } == {
            (
                "new_match:"
                + str(
                    match[
                        "_id"
                    ]
                )
            ),
        }

        assert all(
            item[
                "read_at"
            ]
            is None
            for item in notifications
        )

        assert emitted == [
            str(
                second
            ),
            str(
                first
            ),
        ]


def test_existing_active_match_does_not_duplicate_durable_notifications(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    emitted = []

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        lambda *, user_id, match: (
            emitted.append(
                str(
                    user_id
                )
            )
        ),
    )

    with app.app_context():
        submit_profile_action(
            actor_id=first,
            target_id=second,
            action="like",
        )

        result = submit_profile_action(
            actor_id=second,
            target_id=first,
            action="like",
        )

        assert (
            result[
                "matched"
            ]
            is True
        )

        match = get_match_between(
            first,
            second,
        )

        assert match is not None

        before = (
            mongo.db.notifications.count_documents(
                {
                    "user_id": {
                        "$in": [
                            first,
                            second,
                        ],
                    },
                    "type": "new_match",
                    "resource_id": (
                        match[
                            "_id"
                        ]
                    ),
                }
            )
        )

        assert before == 2

        repeated = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            repeated[
                "matched"
            ]
            is True
        )

        after = (
            mongo.db.notifications.count_documents(
                {
                    "user_id": {
                        "$in": [
                            first,
                            second,
                        ],
                    },
                    "type": "new_match",
                    "resource_id": (
                        match[
                            "_id"
                        ]
                    ),
                }
            )
        )

        assert after == 2

        assert emitted == [
            str(
                second
            ),
            str(
                first
            ),
        ]


def test_new_match_preference_suppresses_only_disabled_recipient(
    app,
    matching_users,
    monkeypatch,
):
    from app.repositories.notification_preferences_repository import (
        update_notification_preferences_for_user,
    )

    first = matching_users()
    second = matching_users()

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        lambda **kwargs: None,
    )

    with app.app_context():
        update_notification_preferences_for_user(
            first,
            disabled_types=[
                "new_match",
            ],
        )

        submit_profile_action(
            actor_id=first,
            target_id=second,
            action="like",
        )

        result = submit_profile_action(
            actor_id=second,
            target_id=first,
            action="like",
        )

        assert (
            result[
                "matched"
            ]
            is True
        )

        match = get_match_between(
            first,
            second,
        )

        assert match is not None

        notifications = list(
            mongo.db.notifications.find(
                {
                    "user_id": {
                        "$in": [
                            first,
                            second,
                        ],
                    },
                    "type": "new_match",
                    "resource_id": (
                        match[
                            "_id"
                        ]
                    ),
                }
            )
        )

        assert (
            len(
                notifications
            )
            == 1
        )

        assert (
            notifications[
                0
            ][
                "user_id"
            ]
            == second
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": first,
                }
            )
            == 1
        )

        assert (
            mongo.db.notification_preferences.count_documents(
                {
                    "user_id": second,
                }
            )
            == 0
        )


def test_notification_failure_does_not_fail_match_or_realtime_delivery(
    app,
    matching_users,
    monkeypatch,
):
    first = matching_users()
    second = matching_users()

    calls = []

    def fake_create_notification(
        **kwargs,
    ):
        recipient = kwargs[
            "user_id"
        ]

        calls.append(
            (
                "notification",
                str(
                    recipient
                ),
            )
        )

        if recipient == second:
            raise RuntimeError(
                "Simulated Notification failure."
            )

        return {
            "notification": {
                "id": str(
                    ObjectId()
                ),
            },
            "created": True,
        }

    def fake_emit_match_created(
        *,
        user_id,
        match,
    ):
        calls.append(
            (
                "realtime",
                str(
                    user_id
                ),
            )
        )

    monkeypatch.setattr(
        matching_service,
        "create_notification",
        fake_create_notification,
    )

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        fake_emit_match_created,
    )

    with app.app_context():
        first_action = (
            submit_profile_action(
                actor_id=first,
                target_id=second,
                action="like",
            )
        )

        assert (
            first_action[
                "matched"
            ]
            is False
        )

        result = (
            submit_profile_action(
                actor_id=second,
                target_id=first,
                action="like",
            )
        )

        assert (
            result[
                "matched"
            ]
            is True
        )

        stored = get_match_between(
            first,
            second,
        )

        assert stored is not None

        assert (
            stored[
                "status"
            ]
            == "active"
        )

        assert calls == [
            (
                "notification",
                str(
                    second
                ),
            ),
            (
                "notification",
                str(
                    first
                ),
            ),
            (
                "realtime",
                str(
                    second
                ),
            ),
            (
                "realtime",
                str(
                    first
                ),
            ),
        ]


def test_repository_created_false_does_not_create_durable_notifications(
    monkeypatch,
):
    first = ObjectId()
    second = ObjectId()
    match_id = ObjectId()

    match = {
        "_id": match_id,
        "status": "active",
    }

    notification_calls = []
    realtime_calls = []

    monkeypatch.setattr(
        matching_service,
        "_validate_matching_pair",
        lambda actor_id, target_id: (
            first,
            second,
        ),
    )

    monkeypatch.setattr(
        matching_service,
        "get_match_between",
        lambda first_user_id, second_user_id: None,
    )

    monkeypatch.setattr(
        matching_service,
        "upsert_profile_action",
        lambda **kwargs: {
            "action": kwargs[
                "action"
            ],
        },
    )

    monkeypatch.setattr(
        matching_service,
        "get_reciprocal_positive_action",
        lambda **kwargs: {
            "action": "like",
        },
    )

    monkeypatch.setattr(
        matching_service,
        "create_match_if_absent",
        lambda **kwargs: {
            "match": match,
            "created": False,
        },
    )

    monkeypatch.setattr(
        matching_service,
        "serialize_match_for_user",
        lambda stored_match, user_id: {
            "id": str(
                match_id
            ),
            "status": "active",
            "other_user_id": str(
                second
                if user_id == first
                else first
            ),
        },
    )

    monkeypatch.setattr(
        matching_service,
        "create_notification",
        lambda **kwargs: (
            notification_calls.append(
                kwargs
            )
        ),
    )

    monkeypatch.setattr(
        matching_service,
        "emit_match_created",
        lambda **kwargs: (
            realtime_calls.append(
                kwargs
            )
        ),
    )

    result = submit_profile_action(
        actor_id=first,
        target_id=second,
        action="like",
    )

    assert (
        result[
            "matched"
        ]
        is True
    )

    assert notification_calls == []

    assert realtime_calls == []
