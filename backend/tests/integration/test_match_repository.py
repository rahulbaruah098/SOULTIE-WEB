"""Integration tests for the Soultie match repository."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest
from bson import ObjectId

from app import create_app
from app.extensions import mongo
from app.repositories.match_repository import (
    create_match_if_absent,
    get_match_between,
    get_match_by_id_for_user,
    get_profile_action,
    get_reciprocal_positive_action,
    list_matches_for_user,
    unmatch,
    upsert_profile_action,
)
from app.schemas.match import (
    build_pair_key,
)


@pytest.fixture(scope="module")
def app():
    application = create_app()

    with application.app_context():
        yield application


@pytest.fixture(autouse=True)
def cleanup_matching_documents(app):
    tracked_user_ids = []

    yield tracked_user_ids

    with app.app_context():
        if tracked_user_ids:
            mongo.db.profile_actions.delete_many(
                {
                    "$or": [
                        {
                            "actor_id": {
                                "$in": tracked_user_ids,
                            }
                        },
                        {
                            "target_id": {
                                "$in": tracked_user_ids,
                            }
                        },
                    ]
                }
            )

            mongo.db.matches.delete_many(
                {
                    "user_ids": {
                        "$in": tracked_user_ids,
                    }
                }
            )


def as_utc(
    value,
):
    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def make_users(
    tracker,
    count=2,
):
    users = [
        ObjectId()
        for _ in range(count)
    ]

    tracker.extend(
        users
    )

    return users


def test_upsert_profile_action_creates_action(
    app,
    cleanup_matching_documents,
):
    actor, target = make_users(
        cleanup_matching_documents
    )

    timestamp = datetime(
        2026,
        10,
        3,
        20,
        0,
        tzinfo=timezone.utc,
    )

    with app.app_context():
        action = upsert_profile_action(
            actor_id=actor,
            target_id=target,
            action="like",
            now=timestamp,
        )

        assert action is not None
        assert action["actor_id"] == actor
        assert action["target_id"] == target
        assert action["action"] == "like"
        assert as_utc(action["created_at"]) == timestamp
        assert as_utc(action["updated_at"]) == timestamp

        stored = get_profile_action(
            actor,
            target,
        )

        assert stored is not None
        assert stored["_id"] == action["_id"]


def test_upsert_profile_action_updates_existing_action_without_duplicate(
    app,
    cleanup_matching_documents,
):
    actor, target = make_users(
        cleanup_matching_documents
    )

    first_time = datetime(
        2026,
        10,
        3,
        20,
        5,
        tzinfo=timezone.utc,
    )

    second_time = (
        first_time
        + timedelta(
            minutes=5
        )
    )

    with app.app_context():
        first = upsert_profile_action(
            actor_id=actor,
            target_id=target,
            action="like",
            now=first_time,
        )

        second = upsert_profile_action(
            actor_id=actor,
            target_id=target,
            action="super_like",
            now=second_time,
        )

        assert first["_id"] == second["_id"]
        assert second["action"] == "super_like"
        assert as_utc(second["created_at"]) == first_time
        assert as_utc(second["updated_at"]) == second_time

        count = mongo.db.profile_actions.count_documents(
            {
                "actor_id": actor,
                "target_id": target,
            }
        )

        assert count == 1


@pytest.mark.parametrize(
    "reciprocal_action,expected",
    [
        ("like", True),
        ("super_like", True),
        ("pass", False),
    ],
)
def test_reciprocal_positive_action_detection(
    app,
    cleanup_matching_documents,
    reciprocal_action,
    expected,
):
    actor, target = make_users(
        cleanup_matching_documents
    )

    with app.app_context():
        upsert_profile_action(
            actor_id=target,
            target_id=actor,
            action=reciprocal_action,
        )

        reciprocal = (
            get_reciprocal_positive_action(
                actor_id=actor,
                target_id=target,
            )
        )

        assert (
            reciprocal is not None
        ) is expected


def test_create_match_is_canonical_and_idempotent(
    app,
    cleanup_matching_documents,
):
    first, second = make_users(
        cleanup_matching_documents
    )

    timestamp = datetime(
        2026,
        10,
        3,
        20,
        15,
        tzinfo=timezone.utc,
    )

    with app.app_context():
        initial = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
            now=timestamp,
        )

        duplicate = create_match_if_absent(
            first_user_id=second,
            second_user_id=first,
            matched_by=first,
            now=timestamp,
        )

        assert initial["created"] is True
        assert duplicate["created"] is False

        assert (
            initial["match"]["_id"]
            == duplicate["match"]["_id"]
        )

        assert (
            initial["match"]["pair_key"]
            == build_pair_key(
                first,
                second,
            )
        )

        count = mongo.db.matches.count_documents(
            {
                "pair_key": build_pair_key(
                    first,
                    second,
                )
            }
        )

        assert count == 1


def test_get_match_between_is_order_independent(
    app,
    cleanup_matching_documents,
):
    first, second = make_users(
        cleanup_matching_documents
    )

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        forward = get_match_between(
            first,
            second,
        )

        reverse = get_match_between(
            second,
            first,
        )

        assert forward is not None
        assert reverse is not None

        assert (
            forward["_id"]
            == created["match"]["_id"]
        )

        assert (
            reverse["_id"]
            == created["match"]["_id"]
        )


def test_get_match_by_id_requires_membership(
    app,
    cleanup_matching_documents,
):
    first, second, outsider = make_users(
        cleanup_matching_documents,
        count=3,
    )

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=first,
        )

        match_id = (
            created["match"]["_id"]
        )

        assert (
            get_match_by_id_for_user(
                match_id,
                first,
            )
            is not None
        )

        assert (
            get_match_by_id_for_user(
                match_id,
                outsider,
            )
            is None
        )


def test_list_matches_returns_only_requested_status(
    app,
    cleanup_matching_documents,
):
    user, active_target, old_target = (
        make_users(
            cleanup_matching_documents,
            count=3,
        )
    )

    with app.app_context():
        active = create_match_if_absent(
            first_user_id=user,
            second_user_id=active_target,
            matched_by=active_target,
        )

        old = create_match_if_absent(
            first_user_id=user,
            second_user_id=old_target,
            matched_by=old_target,
        )

        unmatch(
            match_id=old["match"]["_id"],
            user_id=user,
        )

        active_matches = (
            list_matches_for_user(
                user,
                status="active",
            )
        )

        assert len(
            active_matches
        ) == 1

        assert (
            active_matches[0]["_id"]
            == active["match"]["_id"]
        )

        unmatched = list_matches_for_user(
            user,
            status="unmatched",
        )

        assert len(
            unmatched
        ) == 1

        assert (
            unmatched[0]["_id"]
            == old["match"]["_id"]
        )


def test_unmatch_is_idempotent_and_preserves_history(
    app,
    cleanup_matching_documents,
):
    first, second = make_users(
        cleanup_matching_documents
    )

    timestamp = datetime(
        2026,
        10,
        3,
        20,
        30,
        tzinfo=timezone.utc,
    )

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=second,
        )

        match_id = (
            created["match"]["_id"]
        )

        first_result = unmatch(
            match_id=match_id,
            user_id=first,
            now=timestamp,
        )

        second_result = unmatch(
            match_id=match_id,
            user_id=first,
            now=(
                timestamp
                + timedelta(
                    minutes=10
                )
            ),
        )

        assert first_result is not None
        assert second_result is not None

        assert (
            first_result["status"]
            == "unmatched"
        )

        assert (
            first_result["unmatched_by"]
            == first
        )

        assert (
            as_utc(
                first_result["unmatched_at"]
            )
            == timestamp
        )

        assert (
            as_utc(
                second_result["unmatched_at"]
            )
            == timestamp
        )

        assert (
            second_result["unmatched_by"]
            == first
        )


def test_non_member_cannot_unmatch(
    app,
    cleanup_matching_documents,
):
    first, second, outsider = make_users(
        cleanup_matching_documents,
        count=3,
    )

    with app.app_context():
        created = create_match_if_absent(
            first_user_id=first,
            second_user_id=second,
            matched_by=first,
        )

        result = unmatch(
            match_id=(
                created[
                    "match"
                ][
                    "_id"
                ]
            ),
            user_id=outsider,
        )

        assert result is None

        stored = get_match_between(
            first,
            second,
        )

        assert (
            stored["status"]
            == "active"
        )


def test_invalid_ids_fail_safely(
    app,
):
    with app.app_context():
        assert (
            get_profile_action(
                "invalid",
                ObjectId(),
            )
            is None
        )

        assert (
            get_match_between(
                "invalid",
                ObjectId(),
            )
            is None
        )

        assert (
            get_match_by_id_for_user(
                "invalid",
                ObjectId(),
            )
            is None
        )

        assert (
            list_matches_for_user(
                "invalid"
            )
            == []
        )
