
"""Matching M2 route and preference validation tests."""

import pytest

from app.api.v1.chats.routes import _m2_validate_preferences


def test_m2_all_six_dimensions():
    value = _m2_validate_preferences({
        "languages": ["Assamese", "English"],
        "festivals": ["Bihu"],
        "food_interests": ["Traditional food"],
        "music_interests": ["Folk music"],
        "lifestyle": ["Outdoor"],
        "relationship_goals": ["Long-term"],
    })
    assert len(value) == 6
    assert value["festivals"] == ["Bihu"]


def test_m2_deduplicates_normalized_choices():
    value = _m2_validate_preferences({
        "languages": ["Assamese", " assamese "]
    })
    assert value["languages"] == ["Assamese"]


@pytest.mark.parametrize("bad", [
    None,
    [],
    {"unknown_field": ["x"]},
    {"languages": "Assamese"},
    {"languages": [""]},
    {"languages": [5]},
    {"languages": ["x" * 61]},
    {"languages": ["a"] * 21},
    {"languages": ["line\nbreak"]},
])
def test_m2_rejects_invalid_choices(bad):
    with pytest.raises(ValueError):
        _m2_validate_preferences(bad)
