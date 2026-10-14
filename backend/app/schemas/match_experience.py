"""Safe response contracts for Xuoroni Match Experience."""

MATCH_EXPERIENCE_VERSION = "v1"

MATCH_CREATED_EVENT = (
    "match.created"
)

ICEBREAKER_CATEGORIES = frozenset(
    {
        "culture",
        "vibe",
        "festival",
        "interest",
        "profile_prompt",
        "compatibility",
        "fun",
    }
)

ICEBREAKER_MAX_ITEMS = 6

ICEBREAKER_TEXT_MAX_LENGTH = 240

ICEBREAKER_CODE_MAX_LENGTH = 80


def _required_text(
    value,
    *,
    field_name,
    max_length,
):
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            f"{field_name} must be text."
        )

    normalized = " ".join(
        value.split()
    )

    if not normalized:
        raise ValueError(
            f"{field_name} cannot be empty."
        )

    if (
        len(normalized)
        > max_length
    ):
        raise ValueError(
            (
                f"{field_name} must be at most "
                f"{max_length} characters."
            )
        )

    return normalized


def normalize_icebreaker_category(
    value,
):
    category = _required_text(
        value,
        field_name="Icebreaker category",
        max_length=40,
    ).lower()

    if (
        category
        not in ICEBREAKER_CATEGORIES
    ):
        raise ValueError(
            (
                "Unsupported icebreaker "
                "category."
            )
        )

    return category


def build_icebreaker(
    *,
    code,
    category,
    text,
):
    """
    Build one app-safe conversation starter.

    The service layer is responsible for deciding which
    profile/compatibility information may be used to generate
    the text. The schema deliberately stores no hidden source
    data or sensitive inferred attributes.
    """

    normalized_code = (
        _required_text(
            code,
            field_name="Icebreaker code",
            max_length=(
                ICEBREAKER_CODE_MAX_LENGTH
            ),
        )
        .lower()
        .replace(
            " ",
            "_",
        )
    )

    normalized_category = (
        normalize_icebreaker_category(
            category
        )
    )

    normalized_text = (
        _required_text(
            text,
            field_name="Icebreaker text",
            max_length=(
                ICEBREAKER_TEXT_MAX_LENGTH
            ),
        )
    )

    return {
        "code": normalized_code,
        "category": (
            normalized_category
        ),
        "text": normalized_text,
    }


def build_chat_availability(
    *,
    available,
    conversation_id=None,
    unlock_required=False,
):
    """
    Describe whether Chat can currently be used for the match.

    Phase 2H does not introduce an artificial conversation
    gate. unlock_required exists in the mobile contract so a
    future mutually agreed workflow can be added without
    changing the response shape.
    """

    if not isinstance(
        available,
        bool,
    ):
        raise ValueError(
            "available must be boolean."
        )

    if not isinstance(
        unlock_required,
        bool,
    ):
        raise ValueError(
            (
                "unlock_required must "
                "be boolean."
            )
        )

    normalized_conversation_id = None

    if conversation_id is not None:
        normalized_conversation_id = (
            _required_text(
                str(
                    conversation_id
                ),
                field_name=(
                    "Conversation ID"
                ),
                max_length=128,
            )
        )

    if (
        not available
        and normalized_conversation_id
        is not None
    ):
        raise ValueError(
            (
                "Unavailable Chat cannot "
                "expose a conversation ID."
            )
        )

    if (
        unlock_required
        and available
    ):
        raise ValueError(
            (
                "Chat cannot be immediately "
                "available while an unlock "
                "is still required."
            )
        )

    return {
        "available": available,
        "conversation_id": (
            normalized_conversation_id
        ),
        "unlock_required": (
            unlock_required
        ),
    }


def build_match_experience(
    *,
    match,
    compatibility,
    icebreakers,
    chat,
):
    """
    Build the canonical mobile-facing Match Experience payload.

    All nested objects must already have passed their owning
    service/schema privacy and authorization boundaries.
    """

    if not isinstance(
        match,
        dict,
    ):
        raise ValueError(
            "match must be an object."
        )

    match_id = str(
        match.get(
            "id"
        )
        or ""
    ).strip()

    other_user_id = str(
        match.get(
            "other_user_id"
        )
        or ""
    ).strip()

    if (
        not match_id
        or not other_user_id
    ):
        raise ValueError(
            (
                "match must contain id and "
                "other_user_id."
            )
        )

    if (
        compatibility is not None
        and not isinstance(
            compatibility,
            dict,
        )
    ):
        raise ValueError(
            (
                "compatibility must be "
                "an object or null."
            )
        )

    if not isinstance(
        icebreakers,
        list,
    ):
        raise ValueError(
            "icebreakers must be a list."
        )

    if (
        len(icebreakers)
        > ICEBREAKER_MAX_ITEMS
    ):
        raise ValueError(
            (
                "Too many icebreakers. "
                f"Maximum is "
                f"{ICEBREAKER_MAX_ITEMS}."
            )
        )

    safe_icebreakers = []

    for item in icebreakers:
        if not isinstance(
            item,
            dict,
        ):
            raise ValueError(
                (
                    "Every icebreaker must "
                    "be an object."
                )
            )

        safe_icebreakers.append(
            build_icebreaker(
                code=item.get(
                    "code"
                ),
                category=item.get(
                    "category"
                ),
                text=item.get(
                    "text"
                ),
            )
        )

    if not isinstance(
        chat,
        dict,
    ):
        raise ValueError(
            "chat must be an object."
        )

    safe_chat = (
        build_chat_availability(
            available=chat.get(
                "available"
            ),
            conversation_id=(
                chat.get(
                    "conversation_id"
                )
            ),
            unlock_required=(
                chat.get(
                    "unlock_required",
                    False,
                )
            ),
        )
    )

    return {
        "version": (
            MATCH_EXPERIENCE_VERSION
        ),
        "match": dict(
            match
        ),
        "compatibility": (
            None
            if compatibility is None
            else dict(
                compatibility
            )
        ),
        "icebreakers": (
            safe_icebreakers
        ),
        "chat": safe_chat,
    }


def build_match_created_payload(
    *,
    match,
):
    """
    Build the minimal privacy-safe realtime payload emitted
    when a genuinely new mutual match is created.

    Compatibility/profile/icebreaker data is intentionally not
    copied into the realtime event. The app can fetch the full
    authorized Match Experience endpoint after receiving it.
    """

    if not isinstance(
        match,
        dict,
    ):
        raise ValueError(
            "match must be an object."
        )

    match_id = str(
        match.get(
            "id"
        )
        or ""
    ).strip()

    other_user_id = str(
        match.get(
            "other_user_id"
        )
        or ""
    ).strip()

    if (
        not match_id
        or not other_user_id
    ):
        raise ValueError(
            (
                "match must contain id and "
                "other_user_id."
            )
        )

    return {
        "event": (
            MATCH_CREATED_EVENT
        ),
        "match": dict(
            match
        ),
        "experience_available": True,
    }


__all__ = [
    "ICEBREAKER_CATEGORIES",
    "ICEBREAKER_CODE_MAX_LENGTH",
    "ICEBREAKER_MAX_ITEMS",
    "ICEBREAKER_TEXT_MAX_LENGTH",
    "MATCH_CREATED_EVENT",
    "MATCH_EXPERIENCE_VERSION",
    "build_chat_availability",
    "build_icebreaker",
    "build_match_created_payload",
    "build_match_experience",
    "normalize_icebreaker_category",
]