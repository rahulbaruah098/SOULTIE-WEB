"""Tests for Soultie privacy-safe Match Experience service."""

import pytest

from app.services import (
    match_experience_service as service,
)
from app.services.compatibility_service import (
    CompatibilityServiceError,
)
from app.services.matching_service import (
    MatchingServiceError,
)

from app.services.match_experience_service import (
    DEFAULT_ICEBREAKER_LIMIT,
    SHARED_CULTURE_FIELDS,
    generate_match_icebreakers,
)


def _compatibility(
    *,
    interests=None,
    languages=None,
    festivals=None,
    relationship_intentions=None,
    culture=None,
    lifestyle=None,
):
    return {
        "algorithm_version": "v1",
        "target_user_id": "target-user",
        "score": 88,
        "band": "very_strong",
        "has_sufficient_data": True,
        "data_coverage_percent": 100,
        "dimensions": [],
        "reasons": [],
        "shared": {
            "interests": (
                interests
                if interests is not None
                else []
            ),
            "languages": (
                languages
                if languages is not None
                else []
            ),
            "culture": (
                culture
                if culture is not None
                else {
                    "communities": [],
                    "tribes": [],
                    "heritage_tags": [],
                    "custom_entries": [],
                }
            ),
            "festivals": (
                festivals
                if festivals is not None
                else []
            ),
            "relationship_intentions": (
                relationship_intentions
                if relationship_intentions
                is not None
                else []
            ),
            "lifestyle": (
                lifestyle
                if lifestyle is not None
                else {}
            ),
        },
    }


def test_default_limit_matches_schema_limit():
    assert (
        DEFAULT_ICEBREAKER_LIMIT
        == 6
    )


def test_shared_culture_fields_are_explicit():
    assert SHARED_CULTURE_FIELDS == (
        "communities",
        "tribes",
        "heritage_tags",
        "custom_entries",
    )


def test_icebreakers_are_deterministic():
    compatibility = _compatibility(
        interests=[
            "Music",
            "Travel",
        ],
        languages=[
            "Assamese",
        ],
        festivals=[
            "Bihu",
        ],
        relationship_intentions=[
            "long_term",
        ],
        culture={
            "communities": [
                "Shared community",
            ],
            "tribes": [],
            "heritage_tags": [],
            "custom_entries": [],
        },
    )

    first = generate_match_icebreakers(
        compatibility=compatibility
    )

    second = generate_match_icebreakers(
        compatibility=compatibility
    )

    assert first == second


def test_personalized_starters_follow_stable_order():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Music",
                "Travel",
            ],
            languages=[
                "Assamese",
            ],
            festivals=[
                "Bihu",
            ],
            relationship_intentions=[
                "long_term",
            ],
            culture={
                "communities": [
                    "Shared context",
                ],
                "tribes": [],
                "heritage_tags": [],
                "custom_entries": [],
            },
        )
    )

    assert [
        item["category"]
        for item in result
    ] == [
        "festival",
        "interest",
        "interest",
        "compatibility",
        "culture",
        "compatibility",
    ]

    assert [
        item["code"]
        for item in result
    ] == [
        "shared_festival_1",
        "shared_interest_1",
        "shared_interest_2",
        "shared_language",
        "shared_cultural_context",
        "shared_connection_intent",
    ]


def test_festival_starter_uses_shared_festival():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            festivals=[
                "Bihu",
            ]
        )
    )

    assert (
        result[0]["category"]
        == "festival"
    )

    assert (
        "Bihu"
        in result[0]["text"]
    )


def test_interest_starter_uses_shared_interest():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Photography",
            ]
        )
    )

    assert (
        result[0]["category"]
        == "interest"
    )

    assert (
        "Photography"
        in result[0]["text"]
    )


def test_language_starter_uses_shared_language():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            languages=[
                "Assamese",
            ]
        )
    )

    assert (
        result[0]["code"]
        == "shared_language"
    )

    assert (
        "Assamese"
        in result[0]["text"]
    )


def test_relationship_intent_is_not_echoed_directly():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            relationship_intentions=[
                "VERY_PRIVATE_INTERNAL_VALUE",
            ]
        )
    )

    combined = " ".join(
        item["text"]
        for item in result
    )

    assert (
        "VERY_PRIVATE_INTERNAL_VALUE"
        not in combined
    )

    assert (
        result[0]["code"]
        == "shared_connection_intent"
    )


@pytest.mark.parametrize(
    "field",
    SHARED_CULTURE_FIELDS,
)
def test_private_cultural_labels_are_not_echoed(
    field,
):
    culture = {
        "communities": [],
        "tribes": [],
        "heritage_tags": [],
        "custom_entries": [],
    }

    culture[field] = [
        "PRIVATE_CULTURAL_LABEL"
    ]

    result = generate_match_icebreakers(
        compatibility=_compatibility(
            culture=culture
        )
    )

    cultural = next(
        item
        for item in result
        if (
            item["code"]
            == "shared_cultural_context"
        )
    )

    assert (
        cultural["category"]
        == "culture"
    )

    assert (
        "PRIVATE_CULTURAL_LABEL"
        not in cultural["text"]
    )


def test_no_cultural_starter_without_shared_culture():
    result = generate_match_icebreakers(
        compatibility=_compatibility()
    )

    assert all(
        item["category"]
        != "culture"
        for item in result
    )


def test_empty_compatibility_uses_safe_fallbacks():
    result = generate_match_icebreakers(
        compatibility=None
    )

    assert result == [
        {
            "code": "vibe_plan",
            "category": "vibe",
            "text": (
                "What's your ideal vibe for getting to know "
                "someone\u2014coffee, a walk, music, food, or "
                "something spontaneous?"
            ),
        },
        {
            "code": "fun_local_pick",
            "category": "fun",
            "text": (
                "If you had one free evening with no plans, "
                "what would you choose to do?"
            ),
        },
    ]


@pytest.mark.parametrize(
    "compatibility",
    [
        {},
        {
            "shared": None,
        },
        {
            "shared": [],
        },
        "invalid",
        None,
    ],
)
def test_malformed_or_missing_shared_data_fails_safe(
    compatibility,
):
    result = generate_match_icebreakers(
        compatibility=compatibility
    )

    assert [
        item["code"]
        for item in result
    ] == [
        "vibe_plan",
        "fun_local_pick",
    ]


def test_text_lists_are_deduplicated_case_insensitively():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Music",
                " music ",
                "MUSIC",
                "Travel",
            ]
        )
    )

    interest_items = [
        item
        for item in result
        if (
            item["category"]
            == "interest"
        )
    ]

    assert len(
        interest_items
    ) == 2

    assert (
        "Music"
        in interest_items[0]["text"]
    )

    assert (
        "Travel"
        in interest_items[1]["text"]
    )


def test_only_first_two_festivals_are_used():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            festivals=[
                "Festival One",
                "Festival Two",
                "Festival Three",
            ]
        )
    )

    festival_items = [
        item
        for item in result
        if (
            item["category"]
            == "festival"
        )
    ]

    assert len(
        festival_items
    ) == 2

    combined = " ".join(
        item["text"]
        for item in festival_items
    )

    assert (
        "Festival One"
        in combined
    )

    assert (
        "Festival Two"
        in combined
    )

    assert (
        "Festival Three"
        not in combined
    )


def test_only_first_two_interests_are_used():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Music",
                "Travel",
                "Cooking",
            ]
        )
    )

    interest_items = [
        item
        for item in result
        if (
            item["category"]
            == "interest"
        )
    ]

    assert len(
        interest_items
    ) == 2

    combined = " ".join(
        item["text"]
        for item in interest_items
    )

    assert "Music" in combined
    assert "Travel" in combined
    assert "Cooking" not in combined


def test_only_first_shared_language_is_used():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            languages=[
                "Assamese",
                "English",
            ]
        )
    )

    language_items = [
        item
        for item in result
        if (
            item["code"]
            == "shared_language"
        )
    ]

    assert len(
        language_items
    ) == 1

    assert (
        "Assamese"
        in language_items[0]["text"]
    )

    assert (
        "English"
        not in language_items[0]["text"]
    )


def test_limit_preserves_priority_order():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Music",
                "Travel",
            ],
            languages=[
                "Assamese",
            ],
            festivals=[
                "Bihu",
            ],
            culture={
                "communities": [
                    "Shared context",
                ],
                "tribes": [],
                "heritage_tags": [],
                "custom_entries": [],
            },
        ),
        limit=3,
    )

    assert [
        item["code"]
        for item in result
    ] == [
        "shared_festival_1",
        "shared_interest_1",
        "shared_interest_2",
    ]


@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        7,
        100,
    ],
)
def test_out_of_range_limit_is_rejected(
    limit,
):
    with pytest.raises(
        ValueError,
        match=(
            "Icebreaker limit must be between"
        ),
    ):
        generate_match_icebreakers(
            compatibility=None,
            limit=limit,
        )


@pytest.mark.parametrize(
    "limit",
    [
        True,
        False,
        None,
        "invalid",
    ],
)
def test_non_integer_limit_is_rejected(
    limit,
):
    with pytest.raises(
        ValueError,
        match=(
            "Icebreaker limit must be an integer"
        ),
    ):
        generate_match_icebreakers(
            compatibility=None,
            limit=limit,
        )


def test_numeric_string_limit_is_supported():
    result = generate_match_icebreakers(
        compatibility=None,
        limit="1",
    )

    assert len(
        result
    ) == 1

    assert (
        result[0]["code"]
        == "vibe_plan"
    )


def test_invalid_shared_value_types_are_ignored_safely():
    result = generate_match_icebreakers(
        compatibility={
            "shared": {
                "interests": "Music",
                "languages": {
                    "value": "Assamese",
                },
                "festivals": 123,
                "relationship_intentions": True,
                "culture": {
                    "communities": "not-a-list",
                    "tribes": None,
                    "heritage_tags": {},
                    "custom_entries": False,
                },
            }
        }
    )

    assert [
        item["code"]
        for item in result
    ] == [
        "vibe_plan",
        "fun_local_pick",
    ]


def test_generated_codes_are_unique():
    result = generate_match_icebreakers(
        compatibility=_compatibility(
            interests=[
                "Music",
                "Travel",
            ],
            languages=[
                "Assamese",
            ],
            festivals=[
                "Bihu",
                "Ali-Aye-Ligang",
            ],
            relationship_intentions=[
                "long_term",
            ],
            culture={
                "communities": [
                    "Shared context",
                ],
                "tribes": [],
                "heritage_tags": [],
                "custom_entries": [],
            },
        )
    )

    codes = [
        item["code"]
        for item in result
    ]

    assert (
        len(codes)
        == len(
            set(codes)
        )
    )


def test_generator_never_exceeds_requested_limit():
    compatibility = _compatibility(
        interests=[
            "Music",
            "Travel",
        ],
        languages=[
            "Assamese",
        ],
        festivals=[
            "Bihu",
            "Durga Puja",
        ],
        relationship_intentions=[
            "long_term",
        ],
        culture={
            "communities": [
                "Shared context",
            ],
            "tribes": [],
            "heritage_tags": [],
            "custom_entries": [],
        },
    )

    for limit in range(
        1,
        DEFAULT_ICEBREAKER_LIMIT + 1,
    ):
        result = (
            generate_match_icebreakers(
                compatibility=compatibility,
                limit=limit,
            )
        )

        assert (
            len(result)
            <= limit
        )


def _active_match(
    **overrides,
):
    match = {
        "id": "match-123",
        "status": "active",
        "other_user_id": "target-user",
        "matched_at": (
            "2026-10-14T10:00:00+00:00"
        ),
        "updated_at": (
            "2026-10-14T10:00:00+00:00"
        ),
    }

    match.update(
        overrides
    )

    return match


def test_get_match_experience_no_conversation_is_chat_ready(
    monkeypatch,
):
    calls = {}

    match = _active_match()

    compatibility = _compatibility(
        interests=[
            "Music",
        ],
        festivals=[
            "Bihu",
        ],
    )

    def fake_get_match(
        *,
        user_id,
        match_id,
    ):
        calls["match"] = {
            "user_id": user_id,
            "match_id": match_id,
        }

        return match

    def fake_compatibility(
        *,
        requester_id,
        target_user_id,
    ):
        calls["compatibility"] = {
            "requester_id": requester_id,
            "target_user_id": target_user_id,
        }

        return compatibility

    def fake_conversation(
        match_id,
    ):
        calls["conversation_match_id"] = (
            match_id
        )

        return None

    monkeypatch.setattr(
        service,
        "get_my_match",
        fake_get_match,
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        fake_compatibility,
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        fake_conversation,
    )

    result = (
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )
    )

    assert calls["match"] == {
        "user_id": "requester-user",
        "match_id": "match-123",
    }

    assert calls["compatibility"] == {
        "requester_id": "requester-user",
        "target_user_id": "target-user",
    }

    assert (
        calls[
            "conversation_match_id"
        ]
        == "match-123"
    )

    assert (
        result[
            "match"
        ][
            "other_user_id"
        ]
        == "target-user"
    )

    assert (
        result[
            "compatibility"
        ]
        == compatibility
    )

    assert result["chat"] == {
        "available": True,
        "conversation_id": None,
        "unlock_required": False,
    }

    assert (
        result[
            "icebreakers"
        ][0][
            "category"
        ]
        == "festival"
    )


def test_get_match_experience_existing_active_conversation(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility(
                interests=[
                    "Music",
                ]
            )
        ),
    )

    conversation = {
        "_id": "conversation-123",
        "match_id": "match-123",
        "status": "active",
    }

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: conversation,
    )

    monkeypatch.setattr(
        service,
        "serialize_conversation_for_user",
        lambda conversation, user_id: {
            "id": "conversation-123",
            "match_id": "match-123",
            "status": "active",
            "other_user_id": "target-user",
        },
    )

    result = (
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )
    )

    assert result["chat"] == {
        "available": True,
        "conversation_id": (
            "conversation-123"
        ),
        "unlock_required": False,
    }


def test_inactive_match_blocks_match_experience(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match(
                status="unmatched"
            )
        ),
    )

    def forbidden_compatibility(
        **kwargs,
    ):
        pytest.fail(
            (
                "Compatibility must not run "
                "for an inactive match."
            )
        )

    def forbidden_chat(
        match_id,
    ):
        pytest.fail(
            (
                "Chat lookup must not run "
                "for an inactive match."
            )
        )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        forbidden_compatibility,
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        forbidden_chat,
    )

    with pytest.raises(
        service.MatchExperienceLifecycleError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_INACTIVE"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_missing_other_user_is_invalid_match_state(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match(
                other_user_id=""
            )
        ),
    )

    def forbidden_compatibility(
        **kwargs,
    ):
        pytest.fail(
            (
                "Compatibility must not run "
                "for corrupt match state."
            )
        )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        forbidden_compatibility,
    )

    with pytest.raises(
        service.MatchExperienceConflictError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_STATE_INVALID"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_matching_not_found_is_translated(
    monkeypatch,
):
    def missing_match(
        **kwargs,
    ):
        raise MatchingServiceError(
            "MATCH_NOT_FOUND",
            "Match was not found.",
            status_code=404,
        )

    monkeypatch.setattr(
        service,
        "get_my_match",
        missing_match,
    )

    with pytest.raises(
        service.MatchExperienceNotFoundError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="missing-match",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_NOT_FOUND"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


def test_matching_validation_error_is_preserved(
    monkeypatch,
):
    def invalid_request(
        **kwargs,
    ):
        raise MatchingServiceError(
            "INVALID_USER_ID",
            "User ID is invalid.",
            status_code=400,
        )

    monkeypatch.setattr(
        service,
        "get_my_match",
        invalid_request,
    )

    with pytest.raises(
        service.MatchExperienceValidationError
    ) as exc_info:
        service.get_match_experience(
            user_id="bad-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "INVALID_USER_ID"
    )

    assert (
        exc_info.value.message
        == "User ID is invalid."
    )

    assert (
        exc_info.value.status_code
        == 400
    )


def test_blocked_or_unavailable_compatibility_fails_closed(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    def unavailable(
        **kwargs,
    ):
        raise CompatibilityServiceError(
            "COMPATIBILITY_NOT_AVAILABLE",
            (
                "Compatibility is not "
                "available for this profile."
            ),
            status_code=404,
        )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        unavailable,
    )

    def forbidden_chat(
        match_id,
    ):
        pytest.fail(
            (
                "Chat must not be exposed "
                "when compatibility access "
                "fails closed."
            )
        )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        forbidden_chat,
    )

    with pytest.raises(
        service.MatchExperienceNotFoundError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_NOT_AVAILABLE"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


def test_compatibility_lifecycle_error_is_translated(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    def inactive_requester(
        **kwargs,
    ):
        raise CompatibilityServiceError(
            "CURRENT_ACCOUNT_INACTIVE",
            (
                "Current account is "
                "not active."
            ),
            status_code=403,
        )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        inactive_requester,
    )

    with pytest.raises(
        service.MatchExperienceLifecycleError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_NOT_AVAILABLE"
    )

    assert (
        exc_info.value.status_code
        == 403
    )


def test_corrupt_chat_membership_fails_closed(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: {
            "_id": "conversation-123",
            "match_id": "match-123",
        },
    )

    def invalid_membership(
        conversation,
        user_id,
    ):
        raise ValueError(
            (
                "User does not belong "
                "to this conversation."
            )
        )

    monkeypatch.setattr(
        service,
        "serialize_conversation_for_user",
        invalid_membership,
    )

    with pytest.raises(
        service.MatchExperienceConflictError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_CHAT_STATE_INVALID"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_chat_match_id_mismatch_is_rejected(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: {
            "_id": "conversation-123",
        },
    )

    monkeypatch.setattr(
        service,
        "serialize_conversation_for_user",
        lambda conversation, user_id: {
            "id": "conversation-123",
            "match_id": "different-match",
            "status": "active",
            "other_user_id": "target-user",
        },
    )

    with pytest.raises(
        service.MatchExperienceConflictError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_CHAT_STATE_INVALID"
    )


def test_chat_other_user_mismatch_is_rejected(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: {
            "_id": "conversation-123",
        },
    )

    monkeypatch.setattr(
        service,
        "serialize_conversation_for_user",
        lambda conversation, user_id: {
            "id": "conversation-123",
            "match_id": "match-123",
            "status": "active",
            "other_user_id": "wrong-user",
        },
    )

    with pytest.raises(
        service.MatchExperienceConflictError
    ) as exc_info:
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )

    assert (
        exc_info.value.code
        == "MATCH_EXPERIENCE_CHAT_STATE_INVALID"
    )


def test_closed_conversation_is_not_reported_as_available(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: {
            "_id": "conversation-123",
        },
    )

    monkeypatch.setattr(
        service,
        "serialize_conversation_for_user",
        lambda conversation, user_id: {
            "id": "conversation-123",
            "match_id": "match-123",
            "status": "closed",
            "other_user_id": "target-user",
        },
    )

    result = (
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
        )
    )

    assert result["chat"] == {
        "available": False,
        "conversation_id": None,
        "unlock_required": False,
    }


def test_match_experience_honors_icebreaker_limit(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "get_my_match",
        lambda **kwargs: (
            _active_match()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_profile_compatibility",
        lambda **kwargs: (
            _compatibility(
                interests=[
                    "Music",
                    "Travel",
                ],
                languages=[
                    "Assamese",
                ],
                festivals=[
                    "Bihu",
                    "Durga Puja",
                ],
                relationship_intentions=[
                    "long_term",
                ],
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_conversation_by_match_id",
        lambda match_id: None,
    )

    result = (
        service.get_match_experience(
            user_id="requester-user",
            match_id="match-123",
            icebreaker_limit=2,
        )
    )

    assert (
        len(
            result[
                "icebreakers"
            ]
        )
        == 2
    )

    assert [
        item["code"]
        for item in result[
            "icebreakers"
        ]
    ] == [
        "shared_festival_1",
        "shared_festival_2",
    ]
