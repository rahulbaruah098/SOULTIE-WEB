"""Schemas and document builders for Soultie matching."""

from datetime import (
    date,
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId


PROFILE_ACTIONS = frozenset(
    {
        "like",
        "super_like",
        "pass",
    }
)

MATCH_STATUSES = frozenset(
    {
        "active",
        "unmatched",
    }
)


def utc_now():
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(value)
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "Invalid user ID."
        ) from None


def normalize_user_pair(
    first_user_id,
    second_user_id,
):
    first = _to_object_id(
        first_user_id
    )

    second = _to_object_id(
        second_user_id
    )

    if first == second:
        raise ValueError(
            "A user cannot match with themselves."
        )

    ordered = sorted(
        (
            first,
            second,
        ),
        key=lambda item: str(
            item
        ),
    )

    return (
        ordered[0],
        ordered[1],
    )


def build_pair_key(
    first_user_id,
    second_user_id,
):
    first, second = (
        normalize_user_pair(
            first_user_id,
            second_user_id,
        )
    )

    return (
        f"{first}:{second}"
    )


def normalize_profile_action(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            "Profile action must be text."
        )

    action = (
        value.strip()
        .lower()
    )

    if action not in PROFILE_ACTIONS:
        raise ValueError(
            "Profile action must be like, super_like, or pass."
        )

    return action


def build_profile_action_document(
    *,
    actor_id,
    target_id,
    action,
    now=None,
):
    actor = _to_object_id(
        actor_id
    )

    target = _to_object_id(
        target_id
    )

    if actor == target:
        raise ValueError(
            "A user cannot act on themselves."
        )

    timestamp = (
        now
        or utc_now()
    )

    return {
        "actor_id": actor,
        "target_id": target,
        "action": (
            normalize_profile_action(
                action
            )
        ),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_match_document(
    *,
    first_user_id,
    second_user_id,
    matched_by,
    now=None,
):
    first, second = (
        normalize_user_pair(
            first_user_id,
            second_user_id,
        )
    )

    matched_by_id = (
        _to_object_id(
            matched_by
        )
    )

    if matched_by_id not in (
        first,
        second,
    ):
        raise ValueError(
            (
                "matched_by must belong "
                "to the matched pair."
            )
        )

    timestamp = (
        now
        or utc_now()
    )

    return {
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
        "matched_by": (
            matched_by_id
        ),
        "matched_at": timestamp,
        "unmatched_at": None,
        "unmatched_by": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _json_safe_value(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return str(
            value
        )

    if isinstance(
        value,
        datetime,
    ):
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )
        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    if isinstance(
        value,
        dict,
    ):
        return {
            key: _json_safe_value(
                item
            )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        list,
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    return value


def serialize_match_for_user(
    match,
    user_id,
):
    if not isinstance(
        match,
        dict,
    ):
        raise ValueError(
            "Match must be an object."
        )

    current_user_id = (
        _to_object_id(
            user_id
        )
    )

    pair = [
        _to_object_id(
            value
        )
        for value in (
            match.get(
                "user_ids"
            )
            or []
        )
    ]

    if (
        len(pair) != 2
        or current_user_id
        not in pair
    ):
        raise ValueError(
            (
                "User does not belong "
                "to this match."
            )
        )

    other_user_id = (
        pair[1]
        if pair[0]
        == current_user_id
        else pair[0]
    )

    payload = {
        "id": match.get(
            "_id"
        ),
        "status": match.get(
            "status"
        ),
        "other_user_id": (
            other_user_id
        ),
        "matched_at": (
            match.get(
                "matched_at"
            )
        ),
        "updated_at": (
            match.get(
                "updated_at"
            )
        ),
    }

    return _json_safe_value(
        payload
    )


__all__ = [
    "MATCH_STATUSES",
    "PROFILE_ACTIONS",
    "build_match_document",
    "build_pair_key",
    "build_profile_action_document",
    "normalize_profile_action",
    "normalize_user_pair",
    "serialize_match_for_user",
]
