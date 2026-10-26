
"""Compatibility for authenticated members of an active conversation.

Both members must opt in. Only explicitly shared preferences are used.
"""
from bson import ObjectId
from app.services.culture_compatibility_service import (
    calculate_culture_compatibility,
    CompatibilityInputError,
)


class CompatibilityUnavailable(ValueError):
    pass


def conversation_compatibility(
    *, database, actor_id, conversation_id
):
    try:
        actor = ObjectId(str(actor_id))
        conversation = ObjectId(str(conversation_id))
    except (ValueError, TypeError):
        raise CompatibilityUnavailable("Compatibility unavailable.")

    chat = database.conversations.find_one({
        "_id": conversation,
        "member_ids": actor,
        "status": "active",
    }, {"member_ids": 1, "status": 1})

    if not chat:
        raise CompatibilityUnavailable("Compatibility unavailable.")

    members = chat.get("member_ids", [])
    if (
        not isinstance(members, list)
        or len(members) != 2
        or len(set(str(x) for x in members)) != 2
        or actor not in members
        or not all(isinstance(x, ObjectId) for x in members)
    ):
        raise CompatibilityUnavailable("Compatibility unavailable.")

    other = next(member for member in members if member != actor)

    active_users = database.users.count_documents({
        "_id": {"$in": members},
        "account_status": "active",
    })
    if active_users != 2:
        raise CompatibilityUnavailable("Compatibility unavailable.")

    profiles = list(database.profiles.find({
        "user_id": {"$in": members},
        "onboarding_status": "completed",
        "profile_status": "active",
        "culture_compatibility_enabled": True,
    }, {
        "user_id": 1,
        "culture_preferences": 1,
        "culture_compatibility_enabled": 1,
    }))

    if len(profiles) != 2:
        raise CompatibilityUnavailable("Compatibility unavailable.")

    by_user = {profile["user_id"]: profile for profile in profiles}
    if set(by_user) != set(members):
        raise CompatibilityUnavailable("Compatibility unavailable.")

    mine = by_user[actor].get("culture_preferences")
    theirs = by_user[other].get("culture_preferences")

    if not isinstance(mine, dict) or not isinstance(theirs, dict):
        raise CompatibilityUnavailable("Compatibility unavailable.")

    try:
        result = calculate_culture_compatibility(mine, theirs)
    except CompatibilityInputError:
        raise CompatibilityUnavailable("Compatibility unavailable.")

    return {
        "conversation_id": str(conversation),
        "compatibility": result,
        "based_on": "mutually_shared_preferences",
        "relationship_success_prediction": False,
    }
