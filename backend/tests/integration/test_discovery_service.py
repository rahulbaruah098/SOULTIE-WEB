"""Tests for Soultie discovery service and privacy-safe serialization."""

from datetime import (
    date,
    datetime,
    timezone,
)

import pytest
from bson import ObjectId

from app.services import discovery_service
from app.services.discovery_service import (
    DiscoveryLifecycleError,
    get_discovery_page,
    serialize_discovery_candidate,
)


def _profile(
    **overrides,
):
    profile = {
        "_id": ObjectId(),
        "user_id": ObjectId(),
        "profile_status": "active",
        "visibility": "visible",
        "onboarding_status": "completed",
        "profile_completion_percent": 100,
        "display_name": "Example User",
        "first_name": "Example",
        "last_name": "Baruah",
        "surname_normalized": "baruah",
        "surname_searchable": True,
        "birth_date": "2000-01-01",
        "gender_identity": "woman",
        "gender_custom_label": None,
        "pronouns": [
            "she/her",
        ],
        "current_city": {
            "city": "Guwahati",
            "state": "Assam",
            "country": "India",
        },
        "hometown": {
            "city": "Jorhat",
            "state": "Assam",
            "country": "India",
        },
        "location": {
            "type": "Point",
            "coordinates": [
                91.7362,
                26.1445,
            ],
        },
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
        "languages": [
            "Assamese",
            "English",
        ],
        "bio": "Example bio",
        "interests": [
            "music",
        ],
        "prompts": [],
        "height_cm": 165,
        "education": {
            "level": "Masters",
            "institution": "Example University",
            "field": "Technology",
        },
        "work": {
            "title": "Developer",
            "organization": "Example Company",
            "industry": "Technology",
        },
        "lifestyle": {
            "smoking": "no",
            "drinking": "no",
            "diet": None,
            "fitness": None,
            "pets": None,
        },
        "relationship_intentions": [
            "long_term",
        ],
        "media": [
            {
                "media_id": "photo-1",
            },
        ],
        "primary_media_id": "photo-1",
        "privacy": {
            "show_age": True,
            "show_distance": True,
            "show_surname": False,
            "show_community": True,
            "show_hometown": True,
            "show_work": True,
            "show_education": True,
        },
        "verification_summary": {
            "phone_verified": True,
            "email_verified": True,
            "selfie_verified": False,
            "identity_verified": False,
            "age_verified": True,
        },
        "last_active_at": datetime(
            2026,
            10,
            3,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        "created_at": datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        "updated_at": datetime(
            2026,
            10,
            3,
            tzinfo=timezone.utc,
        ),
    }

    profile.update(
        overrides
    )

    return profile


def test_candidate_serializer_never_exposes_internal_sensitive_fields():
    profile = _profile()

    result = (
        serialize_discovery_candidate(
            profile,
            requester_location={
                "type": "Point",
                "coordinates": [
                    91.7400,
                    26.1500,
                ],
            },
            today=date(
                2026,
                10,
                3,
            ),
        )
    )

    assert (
        result[
            "user_id"
        ]
        == str(
            profile[
                "user_id"
            ]
        )
    )

    assert result[
        "age"
    ] == 26

    forbidden = {
        "_id",
        "birth_date",
        "last_name",
        "surname_normalized",
        "surname_searchable",
        "location",
        "privacy",
        "profile_status",
        "visibility",
        "onboarding_status",
        "profile_completion_percent",
        "last_active_at",
        "created_at",
        "updated_at",
    }

    assert (
        forbidden
        .intersection(
            result.keys()
        )
        == set()
    )


def test_candidate_privacy_controls_hide_optional_profile_data():
    profile = _profile(
        privacy={
            "show_age": False,
            "show_distance": False,
            "show_surname": False,
            "show_community": False,
            "show_hometown": False,
            "show_work": False,
            "show_education": False,
        }
    )

    result = (
        serialize_discovery_candidate(
            profile,
            requester_location={
                "type": "Point",
                "coordinates": [
                    91.7400,
                    26.1500,
                ],
            },
            today=date(
                2026,
                10,
                3,
            ),
        )
    )

    assert result[
        "age"
    ] is None

    assert result[
        "distance_km"
    ] is None

    assert result[
        "hometown"
    ] == {}

    assert result[
        "work"
    ] == {}

    assert result[
        "education"
    ] == {}

    assert (
        result[
            "culture"
        ][
            "communities"
        ]
        == []
    )

    assert (
        result[
            "culture"
        ][
            "tribes"
        ]
        == []
    )

    assert (
        result[
            "culture"
        ][
            "heritage_tags"
        ]
        == []
    )

    assert (
        result[
            "culture"
        ][
            "custom_entries"
        ]
        == []
    )

    assert (
        result[
            "culture"
        ][
            "festivals"
        ]
        == [
            "Bihu",
        ]
    )


def test_candidate_distance_is_calculated_without_exposing_location():
    profile = _profile()

    result = (
        serialize_discovery_candidate(
            profile,
            requester_location={
                "type": "Point",
                "coordinates": [
                    91.7400,
                    26.1500,
                ],
            },
            today=date(
                2026,
                10,
                3,
            ),
        )
    )

    assert isinstance(
        result[
            "distance_km"
        ],
        float,
    )

    assert (
        result[
            "distance_km"
        ]
        >= 0
    )

    assert (
        "location"
        not in result
    )


def test_disabled_requester_cannot_use_discovery(
    monkeypatch,
):
    monkeypatch.setattr(
        discovery_service,
        "ensure_profile",
        lambda user_id: _profile(
            profile_status="disabled",
        ),
    )

    with pytest.raises(
        DiscoveryLifecycleError
    ) as exc:
        get_discovery_page(
            ObjectId()
        )

    assert (
        exc.value.code
        == "DISCOVERY_PROFILE_DISABLED"
    )


@pytest.mark.parametrize(
    (
        "profile_status",
        "onboarding_status",
    ),
    [
        (
            "draft",
            "in_progress",
        ),
        (
            "active",
            "in_progress",
        ),
    ],
)
def test_incomplete_requester_cannot_use_discovery(
    monkeypatch,
    profile_status,
    onboarding_status,
):
    monkeypatch.setattr(
        discovery_service,
        "ensure_profile",
        lambda user_id: _profile(
            profile_status=(
                profile_status
            ),
            onboarding_status=(
                onboarding_status
            ),
        ),
    )

    with pytest.raises(
        DiscoveryLifecycleError
    ) as exc:
        get_discovery_page(
            ObjectId()
        )

    assert (
        exc.value.code
        == "DISCOVERY_ONBOARDING_INCOMPLETE"
    )


def test_discovery_page_serializes_profiles_and_generates_cursor(
    monkeypatch,
):
    requester_id = ObjectId()

    requester = _profile(
        user_id=requester_id,
        location={
            "type": "Point",
            "coordinates": [
                91.7362,
                26.1445,
            ],
        },
    )

    candidate = _profile(
        user_id=ObjectId(),
        last_active_at=datetime(
            2026,
            10,
            3,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    preferences = {
        "interested_in": [
            "woman",
        ],
        "age_min": 18,
        "age_max": 40,
        "distance_enabled": True,
        "max_distance_km": 50,
        "relationship_intentions": [],
        "communities": [],
        "languages": [],
        "surname_filter": {
            "mode": "none",
            "values": [],
        },
    }

    captured = {}
    touched = []

    monkeypatch.setattr(
        discovery_service,
        "ensure_profile",
        lambda user_id: requester,
    )

    monkeypatch.setattr(
        discovery_service,
        "ensure_discovery_preferences",
        lambda user_id: preferences,
    )

    def fake_find(**kwargs):
        captured.update(
            kwargs
        )

        return {
            "profiles": [
                candidate,
            ],
            "has_more": True,
        }

    monkeypatch.setattr(
        discovery_service,
        "find_discovery_candidate_profiles",
        fake_find,
    )

    monkeypatch.setattr(
        discovery_service,
        "touch_profile_activity",
        lambda user_id: (
            touched.append(
                user_id
            )
            or True
        ),
    )

    result = get_discovery_page(
        requester_id,
        limit="1",
        today=date(
            2026,
            10,
            3,
        ),
    )

    assert (
        result[
            "pagination"
        ][
            "limit"
        ]
        == 1
    )

    assert (
        result[
            "pagination"
        ][
            "returned"
        ]
        == 1
    )

    assert (
        result[
            "pagination"
        ][
            "has_more"
        ]
        is True
    )

    assert (
        result[
            "pagination"
        ][
            "next_cursor"
        ]
        is not None
    )

    assert len(
        result[
            "candidates"
        ]
    ) == 1

    assert (
        "last_active_at"
        not in result[
            "candidates"
        ][0]
    )

    assert (
        "location"
        not in result[
            "candidates"
        ][0]
    )

    assert (
        captured[
            "requester_location"
        ]
        == requester[
            "location"
        ]
    )

    assert (
        touched
        == [
            requester_id,
        ]
    )


def test_discovery_page_without_more_results_has_no_cursor(
    monkeypatch,
):
    requester_id = ObjectId()

    requester = _profile(
        user_id=requester_id,
    )

    monkeypatch.setattr(
        discovery_service,
        "ensure_profile",
        lambda user_id: requester,
    )

    monkeypatch.setattr(
        discovery_service,
        "ensure_discovery_preferences",
        lambda user_id: {},
    )

    monkeypatch.setattr(
        discovery_service,
        "find_discovery_candidate_profiles",
        lambda **kwargs: {
            "profiles": [],
            "has_more": False,
        },
    )

    monkeypatch.setattr(
        discovery_service,
        "touch_profile_activity",
        lambda user_id: True,
    )

    result = get_discovery_page(
        requester_id
    )

    assert (
        result[
            "candidates"
        ]
        == []
    )

    assert (
        result[
            "pagination"
        ][
            "next_cursor"
        ]
        is None
    )

    assert (
        result[
            "pagination"
        ][
            "has_more"
        ]
        is False
    )


def test_discovery_cursor_generation_requires_internal_sort_fields(
    monkeypatch,
):
    requester_id = ObjectId()

    requester = _profile(
        user_id=requester_id,
    )

    candidate = _profile()

    candidate.pop(
        "last_active_at"
    )

    monkeypatch.setattr(
        discovery_service,
        "ensure_profile",
        lambda user_id: requester,
    )

    monkeypatch.setattr(
        discovery_service,
        "ensure_discovery_preferences",
        lambda user_id: {},
    )

    monkeypatch.setattr(
        discovery_service,
        "find_discovery_candidate_profiles",
        lambda **kwargs: {
            "profiles": [
                candidate,
            ],
            "has_more": True,
        },
    )

    with pytest.raises(
        RuntimeError
    ):
        get_discovery_page(
            requester_id
        )
