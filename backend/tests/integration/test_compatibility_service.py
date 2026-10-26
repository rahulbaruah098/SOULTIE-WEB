"""Tests for Soultie deterministic compatibility scoring."""

from copy import deepcopy

import pytest
from bson import ObjectId

from app.services.compatibility_service import (
    COMPATIBILITY_DIMENSION_WEIGHTS,
    COMPATIBILITY_MIN_DATA_COVERAGE_PERCENT,
    calculate_profile_compatibility,
)


def _profile(
    **overrides,
):
    profile = {
        "user_id": ObjectId(),
        "relationship_intentions": [
            "long_term",
        ],
        "interests": [
            "Music",
            "Travel",
        ],
        "languages": [
            "Assamese",
            "English",
        ],
        "culture": {
            "communities": [
                "Assamese",
            ],
            "tribes": [],
            "heritage_tags": [
                "Northeast India",
            ],
            "festivals": [
                "Bihu",
            ],
            "custom_entries": [],
        },
        "lifestyle": {
            "smoking": "no",
            "drinking": "no",
            "diet": "non_vegetarian",
            "fitness": "sometimes",
            "pets": "likes_pets",
        },
        "location": {
            "type": "Point",
            "coordinates": [
                91.7362,
                26.1445,
            ],
        },
        "privacy": {
            "show_community": True,
            "show_distance": True,
        },
    }

    profile.update(
        overrides
    )

    return profile


def _dimension(
    result,
    key,
):
    return next(
        item
        for item in result[
            "dimensions"
        ]
        if item["key"] == key
    )


def _reason_codes(
    result,
):
    return {
        item["code"]
        for item in result[
            "reasons"
        ]
    }


def test_weights_are_canonical_and_total_100():
    assert (
        COMPATIBILITY_DIMENSION_WEIGHTS
        == {
            "relationship_intentions": 25,
            "interests": 20,
            "languages": 15,
            "culture": 10,
            "festivals": 10,
            "lifestyle": 15,
            "distance": 5,
        }
    )

    assert (
        sum(
            COMPATIBILITY_DIMENSION_WEIGHTS.values()
        )
        == 100
    )


def test_fully_aligned_profiles_score_100():
    first = _profile()

    second = _profile(
        relationship_intentions=[
            "LONG_TERM",
        ],
        interests=[
            "music",
            "travel",
        ],
        languages=[
            "assamese",
            "english",
        ],
        culture={
            "communities": [
                "assamese",
            ],
            "tribes": [],
            "heritage_tags": [
                "northeast india",
            ],
            "festivals": [
                "bihu",
            ],
            "custom_entries": [],
        },
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert result["score"] == 100

    assert (
        result["band"]
        == "very_strong"
    )

    assert (
        result[
            "data_coverage_percent"
        ]
        == 100
    )

    assert (
        result[
            "has_sufficient_data"
        ]
        is True
    )

    for item in result[
        "dimensions"
    ]:
        assert item["available"] is True
        assert item["score"] == 100


def test_matching_is_case_and_outer_whitespace_insensitive():
    first = _profile(
        interests=[
            "  Music  ",
            "Travel",
        ],
        languages=[
            " Assamese ",
        ],
    )

    second = _profile(
        interests=[
            "music",
            " travel ",
        ],
        languages=[
            "assamese",
        ],
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert (
        _dimension(
            result,
            "interests",
        )["score"]
        == 100
    )

    assert (
        _dimension(
            result,
            "languages",
        )["score"]
        == 100
    )

    assert result[
        "shared"
    ]["interests"] == [
        "Music",
        "Travel",
    ]

    assert result[
        "shared"
    ]["languages"] == [
        "Assamese",
    ]


def test_single_low_weight_signal_is_insufficient_data():
    first = _profile(
        relationship_intentions=[],
        interests=[
            "Music",
        ],
        languages=[],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    second = _profile(
        relationship_intentions=[],
        interests=[
            "Music",
        ],
        languages=[],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert (
        result[
            "data_coverage_percent"
        ]
        == 20
    )

    assert result["score"] is None

    assert (
        result["band"]
        == "insufficient_data"
    )

    assert (
        result[
            "has_sufficient_data"
        ]
        is False
    )

    assert (
        "MORE_PROFILE_DATA_NEEDED"
        in _reason_codes(
            result
        )
    )


def test_exact_minimum_coverage_is_sufficient():
    assert (
        COMPATIBILITY_MIN_DATA_COVERAGE_PERCENT
        == 35
    )

    first = _profile(
        relationship_intentions=[],
        interests=[
            "Music",
        ],
        languages=[
            "Assamese",
        ],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    second = deepcopy(
        first
    )

    second["user_id"] = ObjectId()

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert (
        result[
            "data_coverage_percent"
        ]
        == 35
    )

    assert result["score"] == 100

    assert (
        result[
            "has_sufficient_data"
        ]
        is True
    )


def test_zero_overlap_is_valid_when_enough_data_exists():
    first = _profile(
        relationship_intentions=[
            "long_term",
        ],
        interests=[
            "Music",
        ],
        languages=[
            "Assamese",
        ],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    second = _profile(
        relationship_intentions=[
            "friendship",
        ],
        interests=[
            "Photography",
        ],
        languages=[
            "Hindi",
        ],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert (
        result[
            "data_coverage_percent"
        ]
        == 60
    )

    assert result["score"] == 0
    assert result["band"] == "limited"

    assert (
        result[
            "has_sufficient_data"
        ]
        is True
    )

    assert (
        "MORE_PROFILE_DATA_NEEDED"
        not in _reason_codes(
            result
        )
    )


def test_weighted_score_uses_only_available_dimensions():
    first = _profile(
        relationship_intentions=[
            "long_term",
        ],
        interests=[
            "Music",
            "Travel",
        ],
        languages=[
            "Assamese",
        ],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    second = _profile(
        relationship_intentions=[
            "long_term",
        ],
        interests=[
            "Music",
            "Photography",
        ],
        languages=[
            "English",
        ],
        culture={
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        lifestyle={},
        location=None,
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    assert (
        _dimension(
            result,
            "relationship_intentions",
        )["score"]
        == 100
    )

    assert (
        _dimension(
            result,
            "interests",
        )["score"]
        == 50
    )

    assert (
        _dimension(
            result,
            "languages",
        )["score"]
        == 0
    )

    assert (
        result[
            "data_coverage_percent"
        ]
        == 60
    )

    # (100*25 + 50*20 + 0*15) / 60
    assert result["score"] == 58


def test_hidden_culture_is_not_scored_or_exposed():
    first = _profile(
        privacy={
            "show_community": False,
            "show_distance": True,
        }
    )

    second = _profile()

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    culture = _dimension(
        result,
        "culture",
    )

    assert culture[
        "available"
    ] is False

    assert culture[
        "score"
    ] is None

    assert (
        result["shared"]["culture"]
        == {
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "custom_entries": [],
        }
    )

    assert (
        "SHARED_CULTURAL_CONTEXT"
        not in _reason_codes(
            result
        )
    )


def test_festivals_remain_independent_of_community_visibility():
    first = _profile(
        privacy={
            "show_community": False,
            "show_distance": False,
        }
    )

    second = _profile()

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    festival = _dimension(
        result,
        "festivals",
    )

    assert festival[
        "available"
    ] is True

    assert festival[
        "score"
    ] == 100

    assert (
        result[
            "shared"
        ]["festivals"]
        == [
            "Bihu",
        ]
    )

    assert (
        "SHARED_FESTIVALS"
        in _reason_codes(
            result
        )
    )


def test_different_cultural_context_does_not_exclude_pair():
    first = _profile(
        culture={
            "communities": [
                "Community A",
            ],
            "tribes": [],
            "heritage_tags": [
                "Heritage A",
            ],
            "festivals": [],
            "custom_entries": [],
        }
    )

    second = _profile(
        culture={
            "communities": [
                "Community B",
            ],
            "tribes": [],
            "heritage_tags": [
                "Heritage B",
            ],
            "festivals": [],
            "custom_entries": [],
        }
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    culture = _dimension(
        result,
        "culture",
    )

    assert culture[
        "available"
    ] is True

    assert culture["score"] == 0

    # Different culture contributes zero to this
    # dimension; it never rejects or hides the pair.
    assert result["score"] is not None

    assert (
        result[
            "has_sufficient_data"
        ]
        is True
    )


def test_hidden_distance_is_not_scored():
    first = _profile(
        privacy={
            "show_community": True,
            "show_distance": False,
        }
    )

    second = _profile()

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    distance = _dimension(
        result,
        "distance",
    )

    assert (
        distance["available"]
        is False
    )

    assert distance["score"] is None

    assert (
        "PRACTICAL_DISTANCE"
        not in _reason_codes(
            result
        )
    )


def test_invalid_location_makes_distance_unavailable():
    first = _profile(
        location={
            "type": "Point",
            "coordinates": [
                500,
                500,
            ],
        }
    )

    second = _profile()

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    distance = _dimension(
        result,
        "distance",
    )

    assert (
        distance["available"]
        is False
    )

    assert distance["score"] is None


def test_nearby_profiles_receive_full_distance_score():
    first = _profile(
        location={
            "type": "Point",
            "coordinates": [
                91.7362,
                26.1445,
            ],
        }
    )

    second = _profile(
        location={
            "type": "Point",
            "coordinates": [
                91.7400,
                26.1500,
            ],
        }
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    distance = _dimension(
        result,
        "distance",
    )

    assert distance[
        "available"
    ] is True

    assert distance["score"] == 100

    assert (
        "PRACTICAL_DISTANCE"
        in _reason_codes(
            result
        )
    )


def test_lifestyle_scores_only_comparable_fields():
    first = _profile(
        lifestyle={
            "smoking": "no",
            "drinking": "no",
            "diet": None,
            "fitness": None,
            "pets": None,
        }
    )

    second = _profile(
        lifestyle={
            "smoking": "no",
            "drinking": "yes",
            "diet": "vegetarian",
            "fitness": None,
            "pets": None,
        }
    )

    result = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    lifestyle = _dimension(
        result,
        "lifestyle",
    )

    assert lifestyle[
        "available"
    ] is True

    assert lifestyle["score"] == 50

    assert lifestyle[
        "evidence_count"
    ] == 1

    assert (
        result["shared"]["lifestyle"]
        == {
            "smoking": "no",
        }
    )


def test_score_is_symmetric_for_same_pair_data():
    first = _profile(
        interests=[
            "Music",
            "Travel",
        ],
        languages=[
            "Assamese",
            "English",
        ],
    )

    second = _profile(
        interests=[
            "Music",
            "Photography",
        ],
        languages=[
            "Assamese",
            "Hindi",
        ],
    )

    first_to_second = (
        calculate_profile_compatibility(
            first,
            second,
        )
    )

    second_to_first = (
        calculate_profile_compatibility(
            second,
            first,
        )
    )

    assert (
        first_to_second["score"]
        == second_to_first["score"]
    )

    assert (
        first_to_second[
            "data_coverage_percent"
        ]
        == second_to_first[
            "data_coverage_percent"
        ]
    )

    assert (
        first_to_second["dimensions"]
        == second_to_first["dimensions"]
    )


def test_target_user_id_is_serialized_as_string():
    target_id = ObjectId()

    result = (
        calculate_profile_compatibility(
            _profile(),
            _profile(
                user_id=target_id
            ),
        )
    )

    assert (
        result["target_user_id"]
        == str(target_id)
    )


def test_profiles_must_be_objects():
    with pytest.raises(
        ValueError,
        match="first_profile",
    ):
        calculate_profile_compatibility(
            None,
            _profile(),
        )

    with pytest.raises(
        ValueError,
        match="second_profile",
    ):
        calculate_profile_compatibility(
            _profile(),
            None,
        )


def test_target_profile_requires_user_id():
    second = _profile()

    second.pop(
        "user_id"
    )

    with pytest.raises(
        ValueError,
        match="second_profile.user_id",
    ):
        calculate_profile_compatibility(
            _profile(),
            second,
        )