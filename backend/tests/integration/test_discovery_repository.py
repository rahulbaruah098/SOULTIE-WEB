"""Integration tests for Soultie discovery repository."""

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
from app.repositories.discovery_repository import (
    find_discovery_candidate_profiles,
    get_discovery_excluded_user_ids,
)
from app.repositories.profile_repository import (
    create_profile,
)
from app.repositories.user_repository import (
    create_user,
)


TEST_DATABASE = (
    "xuoroni_test_discovery_repository"
)


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )


def _preferences(
    **overrides,
):
    preferences = {
        "interested_in": [],
        "age_min": 18,
        "age_max": 99,
        "distance_enabled": False,
        "max_distance_km": 50,
        "relationship_intentions": [],
        "communities": [],
        "languages": [],
        "surname_filter": {
            "mode": "none",
            "values": [],
        },
    }

    preferences.update(
        overrides
    )

    return preferences


def _create_candidate(
    *,
    display_name,
    birth_date="2000-01-01",
    gender_identity="woman",
    relationship_intentions=None,
    communities=None,
    languages=None,
    surname=None,
    surname_searchable=False,
    profile_status="active",
    visibility="visible",
    onboarding_status="completed",
    last_active_at=None,
    location=None,
):
    user = create_user()

    profile = create_profile(
        user["_id"]
    )

    updates = {
        "display_name": display_name,
        "birth_date": birth_date,
        "gender_identity": (
            gender_identity
        ),
        "relationship_intentions": (
            relationship_intentions
            if relationship_intentions
            is not None
            else [
                "long_term",
            ]
        ),
        "culture": {
            "communities": (
                communities
                if communities
                is not None
                else [
                    "Assamese",
                ]
            ),
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        "languages": (
            languages
            if languages
            is not None
            else [
                "Assamese",
            ]
        ),
        "profile_status": (
            profile_status
        ),
        "visibility": visibility,
        "onboarding_status": (
            onboarding_status
        ),
        "profile_completion_percent": 100,
        "surname_normalized": surname,
        "surname_searchable": (
            surname_searchable
        ),
        "last_active_at": (
            last_active_at
            or datetime.now(
                timezone.utc
            )
        ),
    }

    if location is not None:
        updates[
            "location"
        ] = location

    mongo.db.profiles.update_one(
        {
            "_id": profile["_id"],
        },
        {
            "$set": updates,
        },
    )

    return (
        user,
        mongo.db.profiles.find_one(
            {
                "_id": profile["_id"],
            }
        ),
    )


def _user_ids(
    result,
):
    return {
        profile[
            "user_id"
        ]
        for profile in result[
            "profiles"
        ]
    }


def test_discovery_requires_active_visible_completed_profile(
    app,
):
    with app.app_context():
        requester = create_user()

        _, eligible = (
            _create_candidate(
                display_name="Eligible",
            )
        )

        _, draft = (
            _create_candidate(
                display_name="Draft",
                profile_status="draft",
            )
        )

        _, hidden = (
            _create_candidate(
                display_name="Hidden",
                visibility="hidden",
            )
        )

        _, incomplete = (
            _create_candidate(
                display_name="Incomplete",
                onboarding_status=(
                    "in_progress"
                ),
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences()
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert (
            eligible[
                "user_id"
            ]
            in ids
        )

        assert (
            draft[
                "user_id"
            ]
            not in ids
        )

        assert (
            hidden[
                "user_id"
            ]
            not in ids
        )

        assert (
            incomplete[
                "user_id"
            ]
            not in ids
        )


def test_requester_is_never_returned(
    app,
):
    with app.app_context():
        requester_user, requester_profile = (
            _create_candidate(
                display_name="Requester",
            )
        )

        _, candidate = (
            _create_candidate(
                display_name="Candidate",
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester_user["_id"]
                ),
                preferences=(
                    _preferences()
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert (
            requester_profile[
                "user_id"
            ]
            not in ids
        )

        assert (
            candidate[
                "user_id"
            ]
            in ids
        )


def test_age_filter_uses_exact_birth_date_boundaries(
    app,
):
    with app.app_context():
        requester = create_user()

        _, exactly_21 = (
            _create_candidate(
                display_name="Exactly 21",
                birth_date="2005-10-03",
            )
        )

        _, still_20 = (
            _create_candidate(
                display_name="Still 20",
                birth_date="2005-10-04",
            )
        )

        _, already_22 = (
            _create_candidate(
                display_name="Already 22",
                birth_date="2004-10-03",
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        age_min=21,
                        age_max=21,
                    )
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert (
            exactly_21[
                "user_id"
            ]
            in ids
        )

        assert (
            still_20[
                "user_id"
            ]
            not in ids
        )

        assert (
            already_22[
                "user_id"
            ]
            not in ids
        )


def test_explicit_discovery_filters_are_combined(
    app,
):
    with app.app_context():
        requester = create_user()

        _, matching = (
            _create_candidate(
                display_name="Matching",
                birth_date="2000-01-01",
                gender_identity="woman",
                relationship_intentions=[
                    "long_term",
                ],
                communities=[
                    "Assamese",
                ],
                languages=[
                    "Assamese",
                ],
            )
        )

        _create_candidate(
            display_name="Wrong Gender",
            gender_identity="man",
        )

        _create_candidate(
            display_name="Wrong Intention",
            relationship_intentions=[
                "friendship",
            ],
        )

        _create_candidate(
            display_name="Wrong Community",
            communities=[
                "Other Community",
            ],
        )

        _create_candidate(
            display_name="Wrong Language",
            languages=[
                "English",
            ],
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        interested_in=[
                            "woman",
                        ],
                        age_min=21,
                        age_max=35,
                        relationship_intentions=[
                            "long_term",
                        ],
                        communities=[
                            "Assamese",
                        ],
                        languages=[
                            "Assamese",
                        ],
                    )
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert ids == {
            matching[
                "user_id"
            ],
        }


def test_surname_include_requires_searchable_opt_in(
    app,
):
    with app.app_context():
        requester = create_user()

        _, searchable_match = (
            _create_candidate(
                display_name=(
                    "Searchable Baruah"
                ),
                surname="baruah",
                surname_searchable=True,
            )
        )

        _, hidden_match = (
            _create_candidate(
                display_name=(
                    "Hidden Baruah"
                ),
                surname="baruah",
                surname_searchable=False,
            )
        )

        _, searchable_other = (
            _create_candidate(
                display_name=(
                    "Searchable Gogoi"
                ),
                surname="gogoi",
                surname_searchable=True,
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        surname_filter={
                            "mode": "include",
                            "values": [
                                "baruah",
                            ],
                        }
                    )
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert ids == {
            searchable_match[
                "user_id"
            ],
        }

        assert (
            hidden_match[
                "user_id"
            ]
            not in ids
        )

        assert (
            searchable_other[
                "user_id"
            ]
            not in ids
        )


def test_surname_exclude_does_not_inspect_private_surname(
    app,
):
    with app.app_context():
        requester = create_user()

        _, searchable_blocked = (
            _create_candidate(
                display_name=(
                    "Searchable Baruah"
                ),
                surname="baruah",
                surname_searchable=True,
            )
        )

        _, private_baruah = (
            _create_candidate(
                display_name=(
                    "Private Baruah"
                ),
                surname="baruah",
                surname_searchable=False,
            )
        )

        _, searchable_gogoi = (
            _create_candidate(
                display_name=(
                    "Searchable Gogoi"
                ),
                surname="gogoi",
                surname_searchable=True,
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        surname_filter={
                            "mode": "exclude",
                            "values": [
                                "baruah",
                            ],
                        }
                    )
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert (
            searchable_blocked[
                "user_id"
            ]
            not in ids
        )

        assert (
            private_baruah[
                "user_id"
            ]
            in ids
        )

        assert (
            searchable_gogoi[
                "user_id"
            ]
            in ids
        )


def test_actions_blocks_and_matches_are_excluded(
    app,
):
    with app.app_context():
        requester = create_user()

        acted_user, _ = (
            _create_candidate(
                display_name="Acted",
            )
        )

        blocked_user, _ = (
            _create_candidate(
                display_name="Blocked",
            )
        )

        blocking_user, _ = (
            _create_candidate(
                display_name="Blocking",
            )
        )

        matched_user, _ = (
            _create_candidate(
                display_name="Matched",
            )
        )

        clean_user, clean_profile = (
            _create_candidate(
                display_name="Clean",
            )
        )

        now = datetime.now(
            timezone.utc
        )

        mongo.db.profile_actions.insert_one(
            {
                "actor_id": (
                    requester["_id"]
                ),
                "target_id": (
                    acted_user["_id"]
                ),
                "action": "like",
                "created_at": now,
            }
        )

        mongo.db.blocks.insert_one(
            {
                "blocker_id": (
                    requester["_id"]
                ),
                "blocked_id": (
                    blocked_user["_id"]
                ),
                "created_at": now,
            }
        )

        mongo.db.blocks.insert_one(
            {
                "blocker_id": (
                    blocking_user["_id"]
                ),
                "blocked_id": (
                    requester["_id"]
                ),
                "created_at": now,
            }
        )

        mongo.db.matches.insert_one(
            {
                "pair_key": (
                    "test-match-pair"
                ),
                "user_ids": [
                    requester["_id"],
                    matched_user["_id"],
                ],
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )

        excluded = set(
            get_discovery_excluded_user_ids(
                requester["_id"]
            )
        )

        assert requester[
            "_id"
        ] in excluded

        assert acted_user[
            "_id"
        ] in excluded

        assert blocked_user[
            "_id"
        ] in excluded

        assert blocking_user[
            "_id"
        ] in excluded

        assert matched_user[
            "_id"
        ] in excluded

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences()
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert ids == {
            clean_user[
                "_id"
            ],
        }

        assert (
            clean_profile[
                "user_id"
            ]
            == clean_user[
                "_id"
            ]
        )


def test_cursor_pagination_is_deterministic(
    app,
):
    with app.app_context():
        requester = create_user()

        base_time = datetime(
            2026,
            10,
            3,
            12,
            0,
            tzinfo=timezone.utc,
        )

        newest_user, _ = (
            _create_candidate(
                display_name="Newest",
                last_active_at=(
                    base_time
                    + timedelta(
                        minutes=3
                    )
                ),
            )
        )

        middle_user, _ = (
            _create_candidate(
                display_name="Middle",
                last_active_at=(
                    base_time
                    + timedelta(
                        minutes=2
                    )
                ),
            )
        )

        oldest_user, _ = (
            _create_candidate(
                display_name="Oldest",
                last_active_at=(
                    base_time
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        first_page = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences()
                ),
                limit=2,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        assert (
            first_page[
                "has_more"
            ]
            is True
        )

        assert [
            item[
                "user_id"
            ]
            for item in first_page[
                "profiles"
            ]
        ] == [
            newest_user[
                "_id"
            ],
            middle_user[
                "_id"
            ],
        ]

        last_profile = (
            first_page[
                "profiles"
            ][-1]
        )

        second_page = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences()
                ),
                limit=2,
                cursor={
                    "last_active_at": (
                        last_profile[
                            "last_active_at"
                        ]
                    ),
                    "profile_id": (
                        last_profile[
                            "_id"
                        ]
                    ),
                },
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        assert [
            item[
                "user_id"
            ]
            for item in second_page[
                "profiles"
            ]
        ] == [
            oldest_user[
                "_id"
            ],
        ]

        assert (
            second_page[
                "has_more"
            ]
            is False
        )


def test_distance_filter_uses_geojson_radius(
    app,
):
    with app.app_context():
        requester = create_user()

        requester_location = {
            "type": "Point",
            "coordinates": [
                91.7362,
                26.1445,
            ],
        }

        near_user, _ = (
            _create_candidate(
                display_name="Near",
                location={
                    "type": "Point",
                    "coordinates": [
                        91.7400,
                        26.1500,
                    ],
                },
            )
        )

        far_user, _ = (
            _create_candidate(
                display_name="Far",
                location={
                    "type": "Point",
                    "coordinates": [
                        91.8933,
                        25.5788,
                    ],
                },
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        distance_enabled=True,
                        max_distance_km=20,
                    )
                ),
                requester_location=(
                    requester_location
                ),
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        ids = _user_ids(
            result
        )

        assert (
            near_user[
                "_id"
            ]
            in ids
        )

        assert (
            far_user[
                "_id"
            ]
            not in ids
        )


def test_distance_filter_is_not_applied_without_permission_location(
    app,
):
    with app.app_context():
        requester = create_user()

        candidate_user, _ = (
            _create_candidate(
                display_name="Candidate",
            )
        )

        result = (
            find_discovery_candidate_profiles(
                requester_id=(
                    requester["_id"]
                ),
                preferences=(
                    _preferences(
                        distance_enabled=True,
                        max_distance_km=20,
                    )
                ),
                requester_location=None,
                limit=20,
                today=date(
                    2026,
                    10,
                    3,
                ),
            )
        )

        assert (
            candidate_user[
                "_id"
            ]
            in _user_ids(
                result
            )
        )
