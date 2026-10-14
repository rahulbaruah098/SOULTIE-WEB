"""MongoDB repository operations for Xuoroni matching."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import (
    DESCENDING,
    ReturnDocument,
)
from pymongo.errors import (
    DuplicateKeyError,
)

from app.extensions import mongo
from app.schemas.match import (
    build_match_document,
    build_pair_key,
    build_profile_action_document,
)


POSITIVE_PROFILE_ACTIONS = (
    "like",
    "super_like",
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
        return None


def get_profile_action(
    actor_id,
    target_id,
):
    actor = _to_object_id(
        actor_id
    )

    target = _to_object_id(
        target_id
    )

    if (
        actor is None
        or target is None
    ):
        return None

    return (
        mongo.db.profile_actions.find_one(
            {
                "actor_id": actor,
                "target_id": target,
            }
        )
    )


def upsert_profile_action(
    *,
    actor_id,
    target_id,
    action,
    now=None,
):
    """Store one latest action per actor/target pair."""

    document = (
        build_profile_action_document(
            actor_id=actor_id,
            target_id=target_id,
            action=action,
            now=now,
        )
    )

    mongo.db.profile_actions.update_one(
        {
            "actor_id": document[
                "actor_id"
            ],
            "target_id": document[
                "target_id"
            ],
        },
        {
            "$set": {
                "action": document[
                    "action"
                ],
                "updated_at": document[
                    "updated_at"
                ],
            },
            "$setOnInsert": {
                "created_at": document[
                    "created_at"
                ],
            },
        },
        upsert=True,
    )

    return get_profile_action(
        document[
            "actor_id"
        ],
        document[
            "target_id"
        ],
    )


def get_reciprocal_positive_action(
    *,
    actor_id,
    target_id,
):
    """Return target -> actor Like/Super Like when one exists."""

    actor = _to_object_id(
        actor_id
    )

    target = _to_object_id(
        target_id
    )

    if (
        actor is None
        or target is None
    ):
        return None

    return (
        mongo.db.profile_actions.find_one(
            {
                "actor_id": target,
                "target_id": actor,
                "action": {
                    "$in": list(
                        POSITIVE_PROFILE_ACTIONS
                    ),
                },
            }
        )
    )


def get_match_between(
    first_user_id,
    second_user_id,
):
    try:
        pair_key = build_pair_key(
            first_user_id,
            second_user_id,
        )

    except ValueError:
        return None

    return mongo.db.matches.find_one(
        {
            "pair_key": pair_key,
        }
    )


def create_match_if_absent(
    *,
    first_user_id,
    second_user_id,
    matched_by,
    now=None,
):
    """
    Create one canonical match.

    The unique pair_key index makes concurrent mutual-like
    requests duplicate-safe.
    """

    document = build_match_document(
        first_user_id=first_user_id,
        second_user_id=second_user_id,
        matched_by=matched_by,
        now=now,
    )

    existing = (
        mongo.db.matches.find_one(
            {
                "pair_key": document[
                    "pair_key"
                ],
            }
        )
    )

    if existing is not None:
        return {
            "match": existing,
            "created": False,
        }

    try:
        result = mongo.db.matches.insert_one(
            document
        )

    except DuplicateKeyError:
        existing = (
            mongo.db.matches.find_one(
                {
                    "pair_key": document[
                        "pair_key"
                    ],
                }
            )
        )

        return {
            "match": existing,
            "created": False,
        }

    created = mongo.db.matches.find_one(
        {
            "_id": result.inserted_id,
        }
    )

    return {
        "match": created,
        "created": True,
    }


def get_match_by_id_for_user(
    match_id,
    user_id,
):
    match_object_id = (
        _to_object_id(
            match_id
        )
    )

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if (
        match_object_id is None
        or user_object_id is None
    ):
        return None

    return mongo.db.matches.find_one(
        {
            "_id": match_object_id,
            "user_ids": user_object_id,
        }
    )


def list_matches_for_user(
    user_id,
    *,
    status="active",
    limit=50,
):
    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return []

    query = {
        "user_ids": user_object_id,
    }

    if status is not None:
        query[
            "status"
        ] = status

    return list(
        mongo.db.matches.find(
            query
        )
        .sort(
            [
                (
                    "updated_at",
                    DESCENDING,
                ),
                (
                    "_id",
                    DESCENDING,
                ),
            ]
        )
        .limit(
            int(limit)
        )
    )


def list_active_match_peer_ids_for_user(
    user_id,
):
    """
    Return the other participant IDs from all currently
    active matches belonging to one user.

    This is intentionally an authorization-oriented lookup:
    it has no arbitrary page-size cap and fails closed for
    malformed stored match membership.
    """

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        return []

    matches = (
        mongo.db.matches.find(
            {
                "user_ids": (
                    user_object_id
                ),
                "status": "active",
            },
            {
                "user_ids": 1,
            },
        )
    )

    peer_ids = []
    seen_peer_ids = set()

    for match in matches:
        stored_user_ids = (
            match.get(
                "user_ids"
            )
        )

        if (
            not isinstance(
                stored_user_ids,
                list,
            )
            or len(
                stored_user_ids
            )
            != 2
        ):
            continue

        normalized_user_ids = [
            _to_object_id(
                value
            )
            for value
            in stored_user_ids
        ]

        if any(
            value is None
            for value
            in normalized_user_ids
        ):
            continue

        first_user_id = (
            normalized_user_ids[
                0
            ]
        )

        second_user_id = (
            normalized_user_ids[
                1
            ]
        )

        if (
            first_user_id
            == second_user_id
        ):
            continue

        if (
            user_object_id
            == first_user_id
        ):
            peer_user_id = (
                second_user_id
            )

        elif (
            user_object_id
            == second_user_id
        ):
            peer_user_id = (
                first_user_id
            )

        else:
            # Defensive fail-closed behavior for malformed
            # or unexpectedly shaped stored membership.
            continue

        if (
            peer_user_id
            in seen_peer_ids
        ):
            continue

        seen_peer_ids.add(
            peer_user_id
        )

        peer_ids.append(
            peer_user_id
        )

    return peer_ids


def unmatch(
    *,
    match_id,
    user_id,
    now=None,
):
    """Mark an active match as unmatched without deleting history."""

    match_object_id = (
        _to_object_id(
            match_id
        )
    )

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if (
        match_object_id is None
        or user_object_id is None
    ):
        return None

    timestamp = (
        now
        or utc_now()
    )

    updated = (
        mongo.db.matches.find_one_and_update(
            {
                "_id": match_object_id,
                "user_ids": user_object_id,
                "status": "active",
            },
            {
                "$set": {
                    "status": "unmatched",
                    "unmatched_at": timestamp,
                    "unmatched_by": (
                        user_object_id
                    ),
                    "updated_at": timestamp,
                }
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    if updated is not None:
        return updated

    # Idempotent repeated unmatch by a match member.
    existing = (
        get_match_by_id_for_user(
            match_object_id,
            user_object_id,
        )
    )

    if (
        existing is not None
        and existing.get(
            "status"
        )
        == "unmatched"
    ):
        return existing

    return None


__all__ = [
    "POSITIVE_PROFILE_ACTIONS",
    "create_match_if_absent",
    "get_match_between",
    "get_match_by_id_for_user",
    "get_profile_action",
    "get_reciprocal_positive_action",
    "list_active_match_peer_ids_for_user",
    "list_matches_for_user",
    "unmatch",
    "upsert_profile_action",
]
