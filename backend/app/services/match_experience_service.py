"""Privacy-safe Match Experience business logic for Soultie."""

from app.repositories.chat_repository import (
    get_conversation_by_match_id,
)
from app.schemas.chat import (
    serialize_conversation_for_user,
)
from app.schemas.match_experience import (
    ICEBREAKER_MAX_ITEMS,
    build_chat_availability,
    build_icebreaker,
    build_match_experience,
)
from app.services.compatibility_service import (
    CompatibilityServiceError,
    get_profile_compatibility,
)
from app.services.matching_service import (
    MatchingServiceError,
    get_my_match,
)


DEFAULT_ICEBREAKER_LIMIT = (
    ICEBREAKER_MAX_ITEMS
)


class MatchExperienceServiceError(
    RuntimeError
):
    """Base Match Experience domain error."""

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


class MatchExperienceValidationError(
    MatchExperienceServiceError
):
    pass


class MatchExperienceNotFoundError(
    MatchExperienceServiceError
):
    pass


class MatchExperienceLifecycleError(
    MatchExperienceServiceError
):
    pass


class MatchExperienceConflictError(
    MatchExperienceServiceError
):
    pass

SHARED_CULTURE_FIELDS = (
    "communities",
    "tribes",
    "heritage_tags",
    "custom_entries",
)


def _clean_text(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        return None

    normalized = " ".join(
        value.split()
    )

    if not normalized:
        return None

    return normalized


def _safe_text_list(
    value,
):
    """
    Return stable, case-insensitively deduplicated text values.

    Compatibility is expected to have already enforced privacy.
    This helper performs defensive normalization only.
    """

    if not isinstance(
        value,
        list,
    ):
        return []

    result = []
    seen = set()

    for item in value:
        normalized = _clean_text(
            item
        )

        if normalized is None:
            continue

        marker = normalized.casefold()

        if marker in seen:
            continue

        seen.add(
            marker
        )

        result.append(
            normalized
        )

    return result


def _shared_mapping(
    compatibility,
):
    if not isinstance(
        compatibility,
        dict,
    ):
        return {}

    shared = compatibility.get(
        "shared"
    )

    if not isinstance(
        shared,
        dict,
    ):
        return {}

    return shared


def _append_unique(
    items,
    seen_codes,
    *,
    code,
    category,
    text,
    limit,
):
    if len(items) >= limit:
        return

    starter = build_icebreaker(
        code=code,
        category=category,
        text=text,
    )

    code_value = starter[
        "code"
    ]

    if code_value in seen_codes:
        return

    seen_codes.add(
        code_value
    )

    items.append(
        starter
    )


def _add_festival_starters(
    items,
    seen_codes,
    shared,
    *,
    limit,
):
    festivals = _safe_text_list(
        shared.get(
            "festivals"
        )
    )

    for index, festival in enumerate(
        festivals[
            :2
        ]
    ):
        _append_unique(
            items,
            seen_codes,
            code=(
                f"shared_festival_{index + 1}"
            ),
            category="festival",
            text=(
                f"You both have {festival} in common. "
                "What do you enjoy most about it?"
            ),
            limit=limit,
        )


def _add_interest_starters(
    items,
    seen_codes,
    shared,
    *,
    limit,
):
    interests = _safe_text_list(
        shared.get(
            "interests"
        )
    )

    for index, interest in enumerate(
        interests[
            :2
        ]
    ):
        _append_unique(
            items,
            seen_codes,
            code=(
                f"shared_interest_{index + 1}"
            ),
            category="interest",
            text=(
                f"You both enjoy {interest}. "
                "What got you interested in it?"
            ),
            limit=limit,
        )


def _add_language_starter(
    items,
    seen_codes,
    shared,
    *,
    limit,
):
    languages = _safe_text_list(
        shared.get(
            "languages"
        )
    )

    if not languages:
        return

    language = languages[0]

    _append_unique(
        items,
        seen_codes,
        code="shared_language",
        category="compatibility",
        text=(
            f"You both speak {language}. "
            "What's a word or expression in it "
            "that you really like?"
        ),
        limit=limit,
    )


def _has_shared_cultural_context(
    shared,
):
    culture = shared.get(
        "culture"
    )

    if not isinstance(
        culture,
        dict,
    ):
        return False

    return any(
        _safe_text_list(
            culture.get(
                field
            )
        )
        for field in SHARED_CULTURE_FIELDS
    )


def _add_culture_starter(
    items,
    seen_codes,
    shared,
    *,
    limit,
):
    """
    Use only the existence of privacy-approved shared culture.

    Exact community/tribe labels are intentionally not repeated
    in generated text, reducing unnecessary sensitive-data
    exposure while still enabling a culture-first conversation.
    """

    if not _has_shared_cultural_context(
        shared
    ):
        return

    _append_unique(
        items,
        seen_codes,
        code="shared_cultural_context",
        category="culture",
        text=(
            "You both share some cultural context. "
            "What's a tradition, food, place, or local "
            "experience you enjoy talking about?"
        ),
        limit=limit,
    )


def _add_relationship_starter(
    items,
    seen_codes,
    shared,
    *,
    limit,
):
    intentions = _safe_text_list(
        shared.get(
            "relationship_intentions"
        )
    )

    if not intentions:
        return

    _append_unique(
        items,
        seen_codes,
        code="shared_connection_intent",
        category="compatibility",
        text=(
            "You're looking for a similar kind of connection. "
            "What helps a new connection feel comfortable "
            "and meaningful to you?"
        ),
        limit=limit,
    )


def _add_vibe_starter(
    items,
    seen_codes,
    *,
    limit,
):
    """
    Generic vibe starter until the dedicated Vibes subsystem
    supplies explicit user-declared vibe data.

    No mood, personality, or other trait is inferred.
    """

    _append_unique(
        items,
        seen_codes,
        code="vibe_plan",
        category="vibe",
        text=(
            "What's your ideal vibe for getting to know "
            "someone—coffee, a walk, music, food, or "
            "something spontaneous?"
        ),
        limit=limit,
    )


def _add_fun_starter(
    items,
    seen_codes,
    *,
    limit,
):
    _append_unique(
        items,
        seen_codes,
        code="fun_local_pick",
        category="fun",
        text=(
            "If you had one free evening with no plans, "
            "what would you choose to do?"
        ),
        limit=limit,
    )


def generate_match_icebreakers(
    *,
    compatibility,
    limit=DEFAULT_ICEBREAKER_LIMIT,
):
    """
    Generate a deterministic set of conversation starters.

    Personalized starters use only the privacy-safe `shared`
    output produced by the Compatibility subsystem.

    Ordering is stable for Flutter clients:
      1. festival
      2. interest
      3. language/common communication
      4. cultural context
      5. relationship intent
      6. generic vibe/fun fallback

    Generic vibe/fun starters never infer user traits.
    """

    if isinstance(
        limit,
        bool,
    ):
        raise ValueError(
            "Icebreaker limit must be an integer."
        )

    try:
        parsed_limit = int(
            limit
        )

    except (
        TypeError,
        ValueError,
    ):
        raise ValueError(
            "Icebreaker limit must be an integer."
        ) from None

    if not (
        1
        <= parsed_limit
        <= ICEBREAKER_MAX_ITEMS
    ):
        raise ValueError(
            (
                "Icebreaker limit must be between "
                f"1 and {ICEBREAKER_MAX_ITEMS}."
            )
        )

    shared = _shared_mapping(
        compatibility
    )

    starters = []
    seen_codes = set()

    _add_festival_starters(
        starters,
        seen_codes,
        shared,
        limit=parsed_limit,
    )

    _add_interest_starters(
        starters,
        seen_codes,
        shared,
        limit=parsed_limit,
    )

    _add_language_starter(
        starters,
        seen_codes,
        shared,
        limit=parsed_limit,
    )

    _add_culture_starter(
        starters,
        seen_codes,
        shared,
        limit=parsed_limit,
    )

    _add_relationship_starter(
        starters,
        seen_codes,
        shared,
        limit=parsed_limit,
    )

    _add_vibe_starter(
        starters,
        seen_codes,
        limit=parsed_limit,
    )

    _add_fun_starter(
        starters,
        seen_codes,
        limit=parsed_limit,
    )

    return starters[
        :parsed_limit
    ]


def _translate_matching_error(
    error,
):
    if (
        error.status_code
        == 400
    ):
        raise MatchExperienceValidationError(
            error.code,
            error.message,
            status_code=400,
        ) from error

    if (
        error.status_code
        == 404
    ):
        raise MatchExperienceNotFoundError(
            "MATCH_EXPERIENCE_NOT_FOUND",
            "Match Experience was not found.",
            status_code=404,
        ) from error

    raise MatchExperienceLifecycleError(
        "MATCH_EXPERIENCE_NOT_AVAILABLE",
        (
            "Match Experience is not "
            "currently available."
        ),
        status_code=error.status_code,
    ) from error


def _translate_compatibility_error(
    error,
):
    if (
        error.status_code
        == 400
    ):
        raise MatchExperienceValidationError(
            error.code,
            error.message,
            status_code=400,
        ) from error

    if (
        error.status_code
        == 404
    ):
        raise MatchExperienceNotFoundError(
            "MATCH_EXPERIENCE_NOT_AVAILABLE",
            (
                "Match Experience is not "
                "available."
            ),
            status_code=404,
        ) from error

    raise MatchExperienceLifecycleError(
        "MATCH_EXPERIENCE_NOT_AVAILABLE",
        (
            "Match Experience is not "
            "currently available."
        ),
        status_code=error.status_code,
    ) from error


def _chat_availability_for_match(
    *,
    user_id,
    match,
):
    """
    Resolve existing Chat state without creating a conversation.

    An active match may legitimately have no conversation yet.
    In that case Chat is available and the normal Chat endpoint
    can create the conversation idempotently when needed.
    """

    match_id = match.get(
        "id"
    )

    other_user_id = str(
        match.get(
            "other_user_id"
        )
        or ""
    ).strip()

    conversation = (
        get_conversation_by_match_id(
            match_id
        )
    )

    if conversation is None:
        return build_chat_availability(
            available=True,
            conversation_id=None,
            unlock_required=False,
        )

    try:
        serialized = (
            serialize_conversation_for_user(
                conversation,
                user_id,
            )
        )

    except ValueError as exc:
        raise MatchExperienceConflictError(
            "MATCH_EXPERIENCE_CHAT_STATE_INVALID",
            (
                "Chat state does not match "
                "this Match Experience."
            ),
            status_code=409,
        ) from exc

    conversation_match_id = str(
        serialized.get(
            "match_id"
        )
        or ""
    ).strip()

    conversation_other_user_id = str(
        serialized.get(
            "other_user_id"
        )
        or ""
    ).strip()

    if (
        conversation_match_id
        != str(
            match_id
        )
        or conversation_other_user_id
        != other_user_id
    ):
        raise MatchExperienceConflictError(
            "MATCH_EXPERIENCE_CHAT_STATE_INVALID",
            (
                "Chat state does not match "
                "this Match Experience."
            ),
            status_code=409,
        )

    if (
        serialized.get(
            "status"
        )
        != "active"
    ):
        return build_chat_availability(
            available=False,
            conversation_id=None,
            unlock_required=False,
        )

    return build_chat_availability(
        available=True,
        conversation_id=serialized.get(
            "id"
        ),
        unlock_required=False,
    )


def get_match_experience(
    *,
    user_id,
    match_id,
    icebreaker_limit=(
        DEFAULT_ICEBREAKER_LIMIT
    ),
):
    """
    Return the authorized Match Experience for one active match.

    This function deliberately reuses the existing Matching,
    Compatibility and Chat boundaries instead of duplicating
    their authorization, privacy or persistence logic.
    """

    try:
        match = get_my_match(
            user_id=user_id,
            match_id=match_id,
        )

    except MatchingServiceError as exc:
        _translate_matching_error(
            exc
        )

    if (
        match.get(
            "status"
        )
        != "active"
    ):
        raise MatchExperienceLifecycleError(
            "MATCH_INACTIVE",
            (
                "Match Experience is available "
                "only for an active match."
            ),
            status_code=409,
        )

    other_user_id = str(
        match.get(
            "other_user_id"
        )
        or ""
    ).strip()

    if not other_user_id:
        raise MatchExperienceConflictError(
            "MATCH_EXPERIENCE_STATE_INVALID",
            (
                "Match Experience state "
                "is invalid."
            ),
            status_code=409,
        )

    try:
        compatibility = (
            get_profile_compatibility(
                requester_id=user_id,
                target_user_id=(
                    other_user_id
                ),
            )
        )

    except CompatibilityServiceError as exc:
        _translate_compatibility_error(
            exc
        )

    try:
        icebreakers = (
            generate_match_icebreakers(
                compatibility=compatibility,
                limit=icebreaker_limit,
            )
        )

    except ValueError as exc:
        raise MatchExperienceValidationError(
            "INVALID_ICEBREAKER_LIMIT",
            str(
                exc
            ),
            status_code=400,
        ) from exc

    chat = (
        _chat_availability_for_match(
            user_id=user_id,
            match=match,
        )
    )

    return build_match_experience(
        match=match,
        compatibility=compatibility,
        icebreakers=icebreakers,
        chat=chat,
    )


__all__ = [
    "DEFAULT_ICEBREAKER_LIMIT",
    "SHARED_CULTURE_FIELDS",
    "MatchExperienceConflictError",
    "MatchExperienceLifecycleError",
    "MatchExperienceNotFoundError",
    "MatchExperienceServiceError",
    "MatchExperienceValidationError",
    "generate_match_icebreakers",
    "get_match_experience",
]
