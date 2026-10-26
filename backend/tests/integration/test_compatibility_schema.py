"""Tests for Soultie compatibility data contracts."""

import pytest

from app.schemas.compatibility import (
    COMPATIBILITY_ALGORITHM_VERSION,
    build_compatibility_dimension,
    build_compatibility_reason,
    build_compatibility_result,
    build_shared_compatibility,
    compatibility_band,
)


def test_compatibility_bands():
    assert compatibility_band(100) == "very_strong"
    assert compatibility_band(85) == "very_strong"
    assert compatibility_band(84) == "strong"
    assert compatibility_band(70) == "strong"
    assert compatibility_band(69) == "good"
    assert compatibility_band(55) == "good"
    assert compatibility_band(54) == "some"
    assert compatibility_band(40) == "some"
    assert compatibility_band(39) == "limited"
    assert compatibility_band(0) == "limited"
    assert (
        compatibility_band(None)
        == "insufficient_data"
    )


def test_compatibility_band_rejects_invalid_score():
    with pytest.raises(ValueError):
        compatibility_band(-1)

    with pytest.raises(ValueError):
        compatibility_band(101)

    with pytest.raises(ValueError):
        compatibility_band(True)


def test_build_available_dimension():
    result = build_compatibility_dimension(
        key="interests",
        score=80,
        weight=20,
        available=True,
        evidence_count=3,
    )

    assert result == {
        "key": "interests",
        "available": True,
        "score": 80,
        "weight": 20,
        "evidence_count": 3,
    }


def test_build_unavailable_dimension():
    result = build_compatibility_dimension(
        key="languages",
        score=None,
        weight=15,
        available=False,
        evidence_count=0,
    )

    assert result["available"] is False
    assert result["score"] is None


def test_unavailable_dimension_cannot_have_score():
    with pytest.raises(ValueError):
        build_compatibility_dimension(
            key="languages",
            score=50,
            weight=15,
            available=False,
        )


def test_unknown_dimension_is_rejected():
    with pytest.raises(ValueError):
        build_compatibility_dimension(
            key="appearance",
            score=90,
            weight=10,
            available=True,
        )


def test_build_reason():
    result = build_compatibility_reason(
        code="SHARED_INTERESTS",
        dimension="interests",
        message="You share some interests.",
    )

    assert result == {
        "code": "SHARED_INTERESTS",
        "dimension": "interests",
        "message": "You share some interests.",
    }


def test_reason_rejects_unknown_dimension():
    with pytest.raises(ValueError):
        build_compatibility_reason(
            code="INVALID",
            dimension="appearance",
            message="Invalid reason.",
        )


def test_shared_compatibility_deduplicates_case_insensitively():
    result = build_shared_compatibility(
        interests=[
            "Music",
            "music",
            "Travel",
        ],
        languages=[
            "Assamese",
            "assamese",
            "English",
        ],
        festivals=[
            "Bihu",
            "bihu",
        ],
    )

    assert result["interests"] == [
        "Music",
        "Travel",
    ]

    assert result["languages"] == [
        "Assamese",
        "English",
    ]

    assert result["festivals"] == [
        "Bihu",
    ]


def test_shared_compatibility_preserves_culture_structure():
    result = build_shared_compatibility(
        communities=["Assamese"],
        tribes=["Example Tribe"],
        heritage_tags=[
            "Northeast India",
        ],
        custom_entries=[
            "Local tradition",
        ],
    )

    assert result["culture"] == {
        "communities": [
            "Assamese",
        ],
        "tribes": [
            "Example Tribe",
        ],
        "heritage_tags": [
            "Northeast India",
        ],
        "custom_entries": [
            "Local tradition",
        ],
    }


def test_result_with_sufficient_data():
    dimension = (
        build_compatibility_dimension(
            key="interests",
            score=75,
            weight=20,
            available=True,
            evidence_count=2,
        )
    )

    shared = (
        build_shared_compatibility(
            interests=[
                "Music",
            ]
        )
    )

    result = build_compatibility_result(
        target_user_id="target-user",
        score=75,
        data_coverage_percent=60,
        dimensions=[
            dimension,
        ],
        reasons=[],
        shared=shared,
    )

    assert (
        result["algorithm_version"]
        == COMPATIBILITY_ALGORITHM_VERSION
    )

    assert (
        result["target_user_id"]
        == "target-user"
    )

    assert result["score"] == 75
    assert result["band"] == "strong"

    assert (
        result["has_sufficient_data"]
        is True
    )

    assert (
        result["data_coverage_percent"]
        == 60
    )


def test_result_can_represent_insufficient_data():
    result = build_compatibility_result(
        target_user_id="target-user",
        score=None,
        data_coverage_percent=10,
    )

    assert result["score"] is None

    assert (
        result["band"]
        == "insufficient_data"
    )

    assert (
        result["has_sufficient_data"]
        is False
    )


def test_result_requires_target_user_id():
    with pytest.raises(ValueError):
        build_compatibility_result(
            target_user_id="",
            score=50,
            data_coverage_percent=50,
        )


def test_result_rejects_invalid_coverage():
    with pytest.raises(ValueError):
        build_compatibility_result(
            target_user_id="target-user",
            score=50,
            data_coverage_percent=101,
        )


def test_lifestyle_must_be_mapping():
    with pytest.raises(ValueError):
        build_shared_compatibility(
            lifestyle=[
                "fitness",
            ]
        )