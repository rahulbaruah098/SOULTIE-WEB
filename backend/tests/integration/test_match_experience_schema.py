"""Tests for Soultie Match Experience schema contracts."""

import pytest

from app.schemas.match_experience import (
    ICEBREAKER_CATEGORIES,
    ICEBREAKER_MAX_ITEMS,
    MATCH_CREATED_EVENT,
    MATCH_EXPERIENCE_VERSION,
    build_chat_availability,
    build_icebreaker,
    build_match_created_payload,
    build_match_experience,
    normalize_icebreaker_category,
)


def _match():
    return {
        "id": "match-123",
        "status": "active",
        "other_user_id": "user-456",
        "matched_at": (
            "2026-10-14T10:00:00+00:00"
        ),
        "updated_at": (
            "2026-10-14T10:00:00+00:00"
        ),
    }


def _icebreaker(
    *,
    code="shared_music",
    category="interest",
    text=(
        "You both enjoy music. "
        "What have you been listening to lately?"
    ),
):
    return build_icebreaker(
        code=code,
        category=category,
        text=text,
    )


def test_match_experience_version():
    assert (
        MATCH_EXPERIENCE_VERSION
        == "v1"
    )


def test_match_created_event_name():
    assert (
        MATCH_CREATED_EVENT
        == "match.created"
    )


def test_icebreaker_categories_are_canonical():
    assert ICEBREAKER_CATEGORIES == frozenset(
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


def test_normalize_icebreaker_category():
    assert (
        normalize_icebreaker_category(
            "  FESTIVAL "
        )
        == "festival"
    )


def test_unknown_icebreaker_category_is_rejected():
    with pytest.raises(
        ValueError,
        match="Unsupported icebreaker category",
    ):
        normalize_icebreaker_category(
            "appearance"
        )


def test_build_icebreaker_normalizes_values():
    result = build_icebreaker(
        code=" Shared Music ",
        category=" INTEREST ",
        text=(
            "  You both enjoy music.   "
            "What are you listening to? "
        ),
    )

    assert result == {
        "code": "shared_music",
        "category": "interest",
        "text": (
            "You both enjoy music. "
            "What are you listening to?"
        ),
    }


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "code",
            "",
        ),
        (
            "category",
            "",
        ),
        (
            "text",
            "",
        ),
    ],
)
def test_icebreaker_required_values(
    field,
    value,
):
    kwargs = {
        "code": "shared_music",
        "category": "interest",
        "text": "Talk about music.",
    }

    kwargs[field] = value

    with pytest.raises(
        ValueError
    ):
        build_icebreaker(
            **kwargs
        )


def test_icebreaker_text_length_is_limited():
    with pytest.raises(
        ValueError,
        match="Icebreaker text must be at most",
    ):
        build_icebreaker(
            code="too_long",
            category="fun",
            text="x" * 241,
        )


def test_chat_can_be_available_before_conversation_creation():
    result = build_chat_availability(
        available=True,
        conversation_id=None,
        unlock_required=False,
    )

    assert result == {
        "available": True,
        "conversation_id": None,
        "unlock_required": False,
    }


def test_chat_can_include_existing_conversation():
    result = build_chat_availability(
        available=True,
        conversation_id="conversation-123",
        unlock_required=False,
    )

    assert (
        result[
            "conversation_id"
        ]
        == "conversation-123"
    )


def test_unavailable_chat_cannot_expose_conversation():
    with pytest.raises(
        ValueError,
        match=(
            "Unavailable Chat cannot "
            "expose a conversation ID"
        ),
    ):
        build_chat_availability(
            available=False,
            conversation_id="conversation-123",
        )


def test_available_chat_cannot_require_unlock():
    with pytest.raises(
        ValueError,
        match=(
            "Chat cannot be immediately "
            "available"
        ),
    ):
        build_chat_availability(
            available=True,
            unlock_required=True,
        )


@pytest.mark.parametrize(
    "available",
    [
        None,
        1,
        "true",
    ],
)
def test_chat_available_requires_boolean(
    available,
):
    with pytest.raises(
        ValueError,
        match="available must be boolean",
    ):
        build_chat_availability(
            available=available
        )


def test_build_match_experience():
    compatibility = {
        "score": 82,
        "band": "strong",
    }

    result = build_match_experience(
        match=_match(),
        compatibility=compatibility,
        icebreakers=[
            _icebreaker()
        ],
        chat={
            "available": True,
            "conversation_id": None,
            "unlock_required": False,
        },
    )

    assert (
        result["version"]
        == "v1"
    )

    assert (
        result[
            "match"
        ][
            "id"
        ]
        == "match-123"
    )

    assert (
        result[
            "compatibility"
        ]
        == compatibility
    )

    assert (
        result[
            "icebreakers"
        ][0][
            "category"
        ]
        == "interest"
    )

    assert (
        result[
            "chat"
        ][
            "available"
        ]
        is True
    )


@pytest.mark.parametrize(
    "match",
    [
        {},
        {
            "id": "match-123",
        },
        {
            "other_user_id": "user-456",
        },
    ],
)
def test_match_experience_requires_pair_identity(
    match,
):
    with pytest.raises(
        ValueError,
        match=(
            "match must contain id and "
            "other_user_id"
        ),
    ):
        build_match_experience(
            match=match,
            compatibility=None,
            icebreakers=[],
            chat={
                "available": True,
                "conversation_id": None,
            },
        )


def test_match_experience_rejects_invalid_compatibility():
    with pytest.raises(
        ValueError,
        match=(
            "compatibility must be "
            "an object or null"
        ),
    ):
        build_match_experience(
            match=_match(),
            compatibility=[],
            icebreakers=[],
            chat={
                "available": True,
            },
        )


def test_match_experience_limits_icebreakers():
    starters = [
        _icebreaker(
            code=f"starter_{index}",
            text=f"Starter number {index}.",
        )
        for index
        in range(
            ICEBREAKER_MAX_ITEMS + 1
        )
    ]

    with pytest.raises(
        ValueError,
        match="Too many icebreakers",
    ):
        build_match_experience(
            match=_match(),
            compatibility=None,
            icebreakers=starters,
            chat={
                "available": True,
            },
        )


def test_match_experience_revalidates_icebreakers():
    with pytest.raises(
        ValueError,
        match="Unsupported icebreaker category",
    ):
        build_match_experience(
            match=_match(),
            compatibility=None,
            icebreakers=[
                {
                    "code": "unsafe_source",
                    "category": "appearance",
                    "text": (
                        "This category must "
                        "not be accepted."
                    ),
                }
            ],
            chat={
                "available": True,
            },
        )


def test_build_match_created_payload():
    result = (
        build_match_created_payload(
            match=_match()
        )
    )

    assert result == {
        "event": "match.created",
        "match": _match(),
        "experience_available": True,
    }


@pytest.mark.parametrize(
    "match",
    [
        {},
        {
            "id": "match-123",
        },
        {
            "other_user_id": "user-456",
        },
    ],
)
def test_match_created_payload_requires_pair_identity(
    match,
):
    with pytest.raises(
        ValueError,
        match=(
            "match must contain id and "
            "other_user_id"
        ),
    ):
        build_match_created_payload(
            match=match
        )