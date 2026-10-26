"""Persistence helpers for Soultie safety relationships."""

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import mongo


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
            str(
                value
            )
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        return None


def pair_is_blocked(
    first_user_id,
    second_user_id,
):
    """
    Return True when either user has blocked the other.

    Blocking is intentionally directional in storage but symmetric for
    access-control decisions.
    """

    first = _to_object_id(
        first_user_id
    )

    second = _to_object_id(
        second_user_id
    )

    if (
        first is None
        or second is None
    ):
        return False

    if first == second:
        return False

    return (
        mongo.db.blocks.find_one(
            {
                "$or": [
                    {
                        "blocker_id": first,
                        "blocked_id": second,
                    },
                    {
                        "blocker_id": second,
                        "blocked_id": first,
                    },
                ]
            },
            {
                "_id": 1,
            },
        )
        is not None
    )


__all__ = [
    "pair_is_blocked",
]
