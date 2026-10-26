"""Tests for Soultie matching schemas."""

from datetime import (
    datetime,
    timezone,
)

import pytest
from bson import ObjectId

from app.schemas.match import (
    PROFILE_ACTIONS,
    build_match_document,
    build_pair_key,
    build_profile_action_document,
    normalize_profile_action,
    normalize_user_pair,
    serialize_match_for_user,
)


def test_profile_actions_include_v1_actions():
    assert PROFILE_ACTIONS == frozenset(
        {
            "like",
            "super_like",
            "pass",
        }
    )


def test_profile_action_is_normalized():
    assert (
        normalize_profile_action(
            "  SUPER_LIKE  "
        )
        == "super_like"
    )

    assert (
        normalize_profile_action(
            " Like "
        )
        == "like"
    )

    assert (
        normalize_profile_action(
            "PASS"
        )
        == "pass"
    )


@pytest.mark.parametrize(
    "value",
    [
        "block",
        "match",
        "",
        None,
        True,
        123,
    ],
)
def test_invalid_profile_action_is_rejected(
    value,
):
    with pytest.raises(
        ValueError
    ):
        normalize_profile_action(
            value
        )


def test_user_pair_and_pair_key_are_canonical():
    first = ObjectId()
    second = ObjectId()

    normalized_one = (
        normalize_user_pair(
            first,
            second,
        )
    )

    normalized_two = (
        normalize_user_pair(
            second,
            first,
        )
    )

    assert (
        normalized_one
        == normalized_two
    )

    assert (
        build_pair_key(
            first,
            second,
        )
        == build_pair_key(
            second,
            first,
        )
    )

    assert (
        build_pair_key(
            first,
            second,
        )
        == (
            f"{normalized_one[0]}:"
            f"{normalized_one[1]}"
        )
    )


def test_invalid_user_id_is_rejected_cleanly():
    with pytest.raises(
        ValueError,
        match="Invalid user ID",
    ):
        normalize_user_pair(
            "not-an-object-id",
            ObjectId(),
        )


def test_same_user_pair_is_rejected():
    user_id = ObjectId()

    with pytest.raises(
        ValueError,
        match="cannot match with themselves",
    ):
        normalize_user_pair(
            user_id,
            user_id,
        )


def test_profile_action_document_is_built_safely():
    actor_id = ObjectId()
    target_id = ObjectId()

    timestamp = datetime(
        2026,
        10,
        3,
        18,
        30,
        tzinfo=timezone.utc,
    )

    document = (
        build_profile_action_document(
            actor_id=actor_id,
            target_id=target_id,
            action=" SUPER_LIKE ",
            now=timestamp,
        )
    )

    assert (
        document[
            "actor_id"
        ]
        == actor_id
    )

    assert (
        document[
            "target_id"
        ]
        == target_id
    )

    assert (
        document[
            "action"
        ]
        == "super_like"
    )

    assert (
        document[
            "created_at"
        ]
        == timestamp
    )

    assert (
        document[
            "updated_at"
        ]
        == timestamp
    )


def test_user_cannot_act_on_themselves():
    user_id = ObjectId()

    with pytest.raises(
        ValueError,
        match="cannot act on themselves",
    ):
        build_profile_action_document(
            actor_id=user_id,
            target_id=user_id,
            action="like",
        )


def test_match_document_is_canonical_and_active():
    first = ObjectId()
    second = ObjectId()

    timestamp = datetime(
        2026,
        10,
        3,
        18,
        45,
        tzinfo=timezone.utc,
    )

    document = build_match_document(
        first_user_id=second,
        second_user_id=first,
        matched_by=second,
        now=timestamp,
    )

    expected_pair = sorted(
        [
            first,
            second,
        ],
        key=lambda value: str(
            value
        ),
    )

    assert (
        document[
            "user_ids"
        ]
        == expected_pair
    )

    assert (
        document[
            "pair_key"
        ]
        == build_pair_key(
            first,
            second,
        )
    )

    assert (
        document[
            "status"
        ]
        == "active"
    )

    assert (
        document[
            "matched_by"
        ]
        == second
    )

    assert (
        document[
            "matched_at"
        ]
        == timestamp
    )

    assert (
        document[
            "unmatched_at"
        ]
        is None
    )

    assert (
        document[
            "unmatched_by"
        ]
        is None
    )


def test_match_rejects_invalid_matched_by():
    first = ObjectId()
    second = ObjectId()
    outsider = ObjectId()

    with pytest.raises(
        ValueError,
        match="matched_by",
    ):
        build_match_document(
            first_user_id=first,
            second_user_id=second,
            matched_by=outsider,
        )


def test_match_serializer_returns_only_user_safe_fields():
    first = ObjectId()
    second = ObjectId()
    match_id = ObjectId()

    timestamp = datetime(
        2026,
        10,
        3,
        19,
        0,
        tzinfo=timezone.utc,
    )

    match = {
        "_id": match_id,
        "pair_key": (
            build_pair_key(
                first,
                second,
            )
        ),
        "user_ids": [
            first,
            second,
        ],
        "status": "active",
        "matched_by": second,
        "matched_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
        "internal_note": (
            "must never be exposed"
        ),
    }

    payload = (
        serialize_match_for_user(
            match,
            first,
        )
    )

    assert (
        payload[
            "id"
        ]
        == str(
            match_id
        )
    )

    assert (
        payload[
            "other_user_id"
        ]
        == str(
            second
        )
    )

    assert (
        payload[
            "status"
        ]
        == "active"
    )

    assert (
        payload[
            "matched_at"
        ]
        == timestamp.isoformat()
    )

    assert set(
        payload.keys()
    ) == {
        "id",
        "status",
        "other_user_id",
        "matched_at",
        "updated_at",
    }


def test_match_serializer_rejects_non_member():
    first = ObjectId()
    second = ObjectId()
    outsider = ObjectId()

    match = {
        "_id": ObjectId(),
        "user_ids": [
            first,
            second,
        ],
        "status": "active",
    }

    with pytest.raises(
        ValueError,
        match="does not belong",
    ):
        serialize_match_for_user(
            match,
            outsider,
        )
