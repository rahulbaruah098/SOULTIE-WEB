"""Business rules for Xuoroni profile actions and matching."""

import logging

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import mongo
from app.repositories.match_repository import (
    create_match_if_absent,
    get_match_between,
    get_match_by_id_for_user,
    get_reciprocal_positive_action,
    list_matches_for_user,
    unmatch,
    upsert_profile_action,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
from app.repositories.user_repository import (
    get_user_by_id,
)
from app.realtime.notifications import (
    emit_match_created,
)
from app.schemas.match import (
    normalize_profile_action,
    serialize_match_for_user,
)


logger = logging.getLogger(
    __name__
)


POSITIVE_ACTIONS = frozenset(
    {
        "like",
        "super_like",
    }
)


class MatchingServiceError(RuntimeError):
    """Base matching-domain error."""

    def __init__(
        self,
        code,
        message,
        *,
        status_code=400,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message
        self.status_code = status_code


class MatchingValidationError(
    MatchingServiceError
):
    pass


class MatchingLifecycleError(
    MatchingServiceError
):
    pass


class MatchingNotFoundError(
    MatchingServiceError
):
    pass


class MatchingConflictError(
    MatchingServiceError
):
    pass


def _to_object_id(
    value,
    *,
    field_name="user_id",
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
        raise MatchingValidationError(
            "INVALID_USER_ID",
            f"{field_name} is invalid.",
            status_code=400,
        ) from None


def _require_user(
    user_id,
    *,
    role_label,
):
    user = get_user_by_id(
        user_id
    )

    if user is None:
        raise MatchingNotFoundError(
            "USER_NOT_FOUND",
            f"{role_label} user was not found.",
            status_code=404,
        )

    if (
        user.get(
            "account_status"
        )
        != "active"
    ):
        raise MatchingLifecycleError(
            "USER_ACCOUNT_INACTIVE",
            (
                f"{role_label} account "
                "is not active."
            ),
            status_code=403,
        )

    return user


def _require_profile(
    user_id,
    *,
    role_label,
    require_visible,
):
    profile = (
        get_profile_by_user_id(
            user_id
        )
    )

    if profile is None:
        raise MatchingNotFoundError(
            "PROFILE_NOT_FOUND",
            (
                f"{role_label} profile "
                "was not found."
            ),
            status_code=404,
        )

    if (
        profile.get(
            "onboarding_status"
        )
        != "completed"
    ):
        raise MatchingLifecycleError(
            "ONBOARDING_INCOMPLETE",
            (
                f"{role_label} onboarding "
                "is incomplete."
            ),
            status_code=409,
        )

    if (
        profile.get(
            "profile_status"
        )
        != "active"
    ):
        raise MatchingLifecycleError(
            "PROFILE_INACTIVE",
            (
                f"{role_label} profile "
                "is not active."
            ),
            status_code=403,
        )

    if (
        require_visible
        and profile.get(
            "visibility"
        )
        != "visible"
    ):
        raise MatchingLifecycleError(
            "PROFILE_NOT_VISIBLE",
            (
                f"{role_label} profile "
                "is not visible."
            ),
            status_code=403,
        )

    return profile


def _pair_is_blocked(
    first_user_id,
    second_user_id,
):
    return (
        mongo.db.blocks.find_one(
            {
                "$or": [
                    {
                        "blocker_id": (
                            first_user_id
                        ),
                        "blocked_id": (
                            second_user_id
                        ),
                    },
                    {
                        "blocker_id": (
                            second_user_id
                        ),
                        "blocked_id": (
                            first_user_id
                        ),
                    },
                ]
            },
            {
                "_id": 1,
            },
        )
        is not None
    )


def _validate_matching_pair(
    actor_id,
    target_id,
):
    actor = _to_object_id(
        actor_id,
        field_name="actor_id",
    )

    target = _to_object_id(
        target_id,
        field_name="target_id",
    )

    if actor == target:
        raise MatchingValidationError(
            "SELF_ACTION_NOT_ALLOWED",
            (
                "A user cannot perform "
                "a profile action on themselves."
            ),
            status_code=400,
        )

    _require_user(
        actor,
        role_label="Actor",
    )

    _require_user(
        target,
        role_label="Target",
    )

    _require_profile(
        actor,
        role_label="Actor",
        require_visible=True,
    )

    _require_profile(
        target,
        role_label="Target",
        require_visible=True,
    )

    if _pair_is_blocked(
        actor,
        target,
    ):
        raise MatchingConflictError(
            "PAIR_BLOCKED",
            (
                "Profile interaction is "
                "not available for this pair."
            ),
            status_code=409,
        )

    return (
        actor,
        target,
    )


def _emit_new_match_created_best_effort(
    *,
    match,
    first_user_id,
    second_user_id,
):
    """
    Emit match.created independently to both match members.

    The match is already durable before this helper runs.
    Realtime delivery is auxiliary: one recipient failing must
    neither fail the durable match nor prevent an attempt for
    the other recipient.
    """

    for recipient_user_id in (
        first_user_id,
        second_user_id,
    ):
        try:
            serialized_match = (
                serialize_match_for_user(
                    match,
                    recipient_user_id,
                )
            )

            emit_match_created(
                user_id=(
                    recipient_user_id
                ),
                match=serialized_match,
            )

        except Exception:
            logger.warning(
                (
                    "Auxiliary match.created "
                    "emission failed after "
                    "durable match creation."
                ),
                exc_info=True,
            )


def submit_profile_action(
    *,
    actor_id,
    target_id,
    action,
):
    """
    Persist Like, Super Like, or Pass.

    A mutual positive action creates exactly one canonical
    match. Pass never creates a match.
    """

    try:
        normalized_action = (
            normalize_profile_action(
                action
            )
        )

    except ValueError as exc:
        raise MatchingValidationError(
            "INVALID_PROFILE_ACTION",
            str(
                exc
            ),
            status_code=400,
        ) from None

    actor, target = (
        _validate_matching_pair(
            actor_id,
            target_id,
        )
    )

    existing_match = (
        get_match_between(
            actor,
            target,
        )
    )

    if (
        existing_match is not None
        and existing_match.get(
            "status"
        )
        == "active"
        and normalized_action
        == "pass"
    ):
        raise MatchingConflictError(
            "MATCH_ALREADY_ACTIVE",
            (
                "This pair is already matched. "
                "Use unmatch instead of pass."
            ),
            status_code=409,
        )

    stored_action = (
        upsert_profile_action(
            actor_id=actor,
            target_id=target,
            action=normalized_action,
        )
    )

    if (
        normalized_action
        not in POSITIVE_ACTIONS
    ):
        return {
            "action": normalized_action,
            "matched": False,
            "match": None,
        }

    if (
        existing_match is not None
        and existing_match.get(
            "status"
        )
        == "active"
    ):
        return {
            "action": normalized_action,
            "matched": True,
            "match": (
                serialize_match_for_user(
                    existing_match,
                    actor,
                )
            ),
        }

    reciprocal = (
        get_reciprocal_positive_action(
            actor_id=actor,
            target_id=target,
        )
    )

    if reciprocal is None:
        return {
            "action": normalized_action,
            "matched": False,
            "match": None,
        }

    created = (
        create_match_if_absent(
            first_user_id=actor,
            second_user_id=target,
            matched_by=actor,
        )
    )

    match = created.get(
        "match"
    )

    if match is None:
        raise MatchingConflictError(
            "MATCH_CREATION_FAILED",
            (
                "The mutual match could "
                "not be created."
            ),
            status_code=409,
        )

    if (
        created.get(
            "created"
        )
        is True
    ):
        _emit_new_match_created_best_effort(
            match=match,
            first_user_id=actor,
            second_user_id=target,
        )

    return {
        "action": normalized_action,
        "matched": (
            match.get(
                "status"
            )
            == "active"
        ),
        "match": (
            serialize_match_for_user(
                match,
                actor,
            )
        ),
    }


def list_my_matches(
    user_id,
    *,
    status="active",
    limit=50,
):
    user = _to_object_id(
        user_id
    )

    _require_user(
        user,
        role_label="Current",
    )

    _require_profile(
        user,
        role_label="Current",
        require_visible=False,
    )

    if status not in {
        "active",
        "unmatched",
    }:
        raise MatchingValidationError(
            "INVALID_MATCH_STATUS",
            (
                "Match status must be "
                "active or unmatched."
            ),
            status_code=400,
        )

    try:
        parsed_limit = int(
            limit
        )

    except (
        TypeError,
        ValueError,
    ):
        raise MatchingValidationError(
            "INVALID_LIMIT",
            "Limit must be an integer.",
            status_code=400,
        ) from None

    if not (
        1
        <= parsed_limit
        <= 100
    ):
        raise MatchingValidationError(
            "INVALID_LIMIT",
            (
                "Limit must be between "
                "1 and 100."
            ),
            status_code=400,
        )

    matches = list_matches_for_user(
        user,
        status=status,
        limit=parsed_limit,
    )

    return {
        "matches": [
            serialize_match_for_user(
                match,
                user,
            )
            for match in matches
        ],
        "returned": len(
            matches
        ),
    }


def get_my_match(
    *,
    user_id,
    match_id,
):
    user = _to_object_id(
        user_id
    )

    match = (
        get_match_by_id_for_user(
            match_id,
            user,
        )
    )

    if match is None:
        raise MatchingNotFoundError(
            "MATCH_NOT_FOUND",
            "Match was not found.",
            status_code=404,
        )

    return serialize_match_for_user(
        match,
        user,
    )


def unmatch_my_match(
    *,
    user_id,
    match_id,
):
    user = _to_object_id(
        user_id
    )

    _require_user(
        user,
        role_label="Current",
    )

    match = (
        get_match_by_id_for_user(
            match_id,
            user,
        )
    )

    if match is None:
        raise MatchingNotFoundError(
            "MATCH_NOT_FOUND",
            "Match was not found.",
            status_code=404,
        )

    updated = unmatch(
        match_id=match["_id"],
        user_id=user,
    )

    if updated is None:
        raise MatchingConflictError(
            "UNMATCH_FAILED",
            (
                "The match could not "
                "be unmatched."
            ),
            status_code=409,
        )

    return serialize_match_for_user(
        updated,
        user,
    )


__all__ = [
    "MatchingConflictError",
    "MatchingLifecycleError",
    "MatchingNotFoundError",
    "MatchingServiceError",
    "MatchingValidationError",
    "get_my_match",
    "list_my_matches",
    "submit_profile_action",
    "unmatch_my_match",
]
