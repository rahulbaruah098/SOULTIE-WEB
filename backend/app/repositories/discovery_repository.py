"""Repository helpers for Soultie profile discovery."""

from datetime import (
    date,
    datetime,
    timezone,
)

from bson import ObjectId
from pymongo import DESCENDING

from app.extensions import mongo
from app.schemas.discovery import (
    DISCOVERY_PROFILE_SOURCE_FIELDS,
)


EARTH_RADIUS_KM = 6378.1


def _to_object_id(
    value,
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
        TypeError,
        ValueError,
    ):
        return None


def _object_id_list(
    values,
):
    result = []
    seen = set()

    for value in (
        values
        or []
    ):
        object_id = _to_object_id(
            value
        )

        if object_id is None:
            continue

        marker = str(
            object_id
        )

        if marker in seen:
            continue

        seen.add(
            marker
        )

        result.append(
            object_id
        )

    return result


def _date_years_ago(
    source_date,
    years,
):
    target_year = (
        source_date.year
        - years
    )

    try:
        return source_date.replace(
            year=target_year
        )

    except ValueError:
        # Handles 29 February safely.
        return source_date.replace(
            year=target_year,
            month=2,
            day=28,
        )


def _birth_date_range(
    *,
    age_min,
    age_max,
    today=None,
):
    current_date = (
        today
        or datetime.now(
            timezone.utc
        ).date()
    )

    if isinstance(
        current_date,
        datetime,
    ):
        current_date = (
            current_date.date()
        )

    if not isinstance(
        current_date,
        date,
    ):
        raise ValueError(
            "today must be a date."
        )

    oldest_exclusive = (
        _date_years_ago(
            current_date,
            int(age_max) + 1,
        )
    )

    youngest_inclusive = (
        _date_years_ago(
            current_date,
            int(age_min),
        )
    )

    return {
        "$gt": (
            oldest_exclusive.isoformat()
        ),
        "$lte": (
            youngest_inclusive.isoformat()
        ),
    }


def _normalize_geojson_point(
    value,
):
    if not isinstance(
        value,
        dict,
    ):
        return None

    if (
        value.get("type")
        != "Point"
    ):
        return None

    coordinates = value.get(
        "coordinates"
    )

    if (
        not isinstance(
            coordinates,
            (list, tuple),
        )
        or len(
            coordinates
        )
        != 2
    ):
        return None

    longitude = coordinates[0]
    latitude = coordinates[1]

    if (
        isinstance(
            longitude,
            bool,
        )
        or isinstance(
            latitude,
            bool,
        )
        or not isinstance(
            longitude,
            (int, float),
        )
        or not isinstance(
            latitude,
            (int, float),
        )
    ):
        return None

    longitude = float(
        longitude
    )

    latitude = float(
        latitude
    )

    if not (
        -180.0
        <= longitude
        <= 180.0
    ):
        return None

    if not (
        -90.0
        <= latitude
        <= 90.0
    ):
        return None

    return {
        "type": "Point",
        "coordinates": [
            longitude,
            latitude,
        ],
    }


def get_discovery_excluded_user_ids(
    requester_id,
):
    """Return users that must never appear in this requester's deck."""

    requester_id = _to_object_id(
        requester_id
    )

    if requester_id is None:
        return []

    excluded = {
        requester_id,
    }

    # Do not repeatedly show profiles the requester has already
    # explicitly acted on.
    acted_on = (
        mongo.db.profile_actions.distinct(
            "target_id",
            {
                "actor_id": requester_id,
            },
        )
    )

    excluded.update(
        _object_id_list(
            acted_on
        )
    )

    # Blocking is symmetric for discovery visibility.
    block_cursor = (
        mongo.db.blocks.find(
            {
                "$or": [
                    {
                        "blocker_id": (
                            requester_id
                        ),
                    },
                    {
                        "blocked_id": (
                            requester_id
                        ),
                    },
                ]
            },
            {
                "blocker_id": 1,
                "blocked_id": 1,
            },
        )
    )

    for block in block_cursor:
        blocker_id = _to_object_id(
            block.get(
                "blocker_id"
            )
        )

        blocked_id = _to_object_id(
            block.get(
                "blocked_id"
            )
        )

        if (
            blocker_id
            == requester_id
        ):
            if blocked_id is not None:
                excluded.add(
                    blocked_id
                )

        elif (
            blocked_id
            == requester_id
        ):
            if blocker_id is not None:
                excluded.add(
                    blocker_id
                )

    # Existing match records also stay out of discovery.
    match_cursor = (
        mongo.db.matches.find(
            {
                "user_ids": requester_id,
            },
            {
                "user_ids": 1,
            },
        )
    )

    for match in match_cursor:
        for user_id in (
            match.get(
                "user_ids"
            )
            or []
        ):
            object_id = _to_object_id(
                user_id
            )

            if (
                object_id is not None
                and object_id
                != requester_id
            ):
                excluded.add(
                    object_id
                )

    return list(
        excluded
    )


def build_discovery_profile_query(
    *,
    requester_id,
    preferences,
    excluded_user_ids=None,
    requester_location=None,
    cursor=None,
    today=None,
):
    """Build a privacy-aware Mongo query for discovery candidates."""

    requester_id = _to_object_id(
        requester_id
    )

    if requester_id is None:
        raise ValueError(
            "Invalid requester ID."
        )

    preferences = (
        preferences
        if isinstance(
            preferences,
            dict,
        )
        else {}
    )

    exclusions = {
        requester_id,
    }

    exclusions.update(
        _object_id_list(
            excluded_user_ids
        )
    )

    clauses = [
        {
            "profile_status": "active",
            "visibility": "visible",
            "onboarding_status": "completed",
            "user_id": {
                "$nin": list(
                    exclusions
                ),
            },
        }
    ]

    age_min = int(
        preferences.get(
            "age_min",
            18,
        )
    )

    age_max = int(
        preferences.get(
            "age_max",
            99,
        )
    )

    clauses.append(
        {
            "birth_date": (
                _birth_date_range(
                    age_min=age_min,
                    age_max=age_max,
                    today=today,
                )
            )
        }
    )

    interested_in = list(
        preferences.get(
            "interested_in"
        )
        or []
    )

    if interested_in:
        clauses.append(
            {
                "gender_identity": {
                    "$in": (
                        interested_in
                    ),
                }
            }
        )

    relationship_intentions = list(
        preferences.get(
            "relationship_intentions"
        )
        or []
    )

    if relationship_intentions:
        clauses.append(
            {
                "relationship_intentions": {
                    "$in": (
                        relationship_intentions
                    ),
                }
            }
        )

    communities = list(
        preferences.get(
            "communities"
        )
        or []
    )

    if communities:
        clauses.append(
            {
                "culture.communities": {
                    "$in": communities,
                }
            }
        )

    languages = list(
        preferences.get(
            "languages"
        )
        or []
    )

    if languages:
        clauses.append(
            {
                "languages": {
                    "$in": languages,
                }
            }
        )

    # Surname filtering is allowed only against profiles that explicitly
    # opted into surname searchability. A hidden surname is never used as
    # a discovery signal.
    surname_filter = (
        preferences.get(
            "surname_filter"
        )
        or {}
    )

    surname_mode = (
        surname_filter.get(
            "mode"
        )
        or "none"
    )

    surname_values = list(
        surname_filter.get(
            "values"
        )
        or []
    )

    if (
        surname_mode
        == "include"
        and surname_values
    ):
        clauses.append(
            {
                "surname_searchable": True,
                "surname_normalized": {
                    "$in": (
                        surname_values
                    ),
                },
            }
        )

    elif (
        surname_mode
        == "exclude"
        and surname_values
    ):
        clauses.append(
            {
                "$or": [
                    {
                        "surname_searchable": {
                            "$ne": True,
                        }
                    },
                    {
                        "surname_normalized": {
                            "$nin": (
                                surname_values
                            ),
                        }
                    },
                ]
            }
        )

    distance_enabled = bool(
        preferences.get(
            "distance_enabled",
            False,
        )
    )

    point = (
        _normalize_geojson_point(
            requester_location
        )
    )

    max_distance_km = (
        preferences.get(
            "max_distance_km"
        )
    )

    if (
        distance_enabled
        and point is not None
        and isinstance(
            max_distance_km,
            (int, float),
        )
        and not isinstance(
            max_distance_km,
            bool,
        )
        and max_distance_km > 0
    ):
        radius_radians = (
            float(
                max_distance_km
            )
            / EARTH_RADIUS_KM
        )

        clauses.append(
            {
                "location": {
                    "$geoWithin": {
                        "$centerSphere": [
                            point[
                                "coordinates"
                            ],
                            radius_radians,
                        ]
                    }
                }
            }
        )

    if cursor:
        cursor_time = cursor.get(
            "last_active_at"
        )

        cursor_profile_id = (
            _to_object_id(
                cursor.get(
                    "profile_id"
                )
            )
        )

        if (
            cursor_time is None
            or cursor_profile_id
            is None
        ):
            raise ValueError(
                "Invalid discovery cursor."
            )

        clauses.append(
            {
                "$or": [
                    {
                        "last_active_at": {
                            "$lt": cursor_time,
                        }
                    },
                    {
                        "last_active_at": (
                            cursor_time
                        ),
                        "_id": {
                            "$lt": (
                                cursor_profile_id
                            ),
                        },
                    },
                ]
            }
        )

    return {
        "$and": clauses,
    }


def find_discovery_candidate_profiles(
    *,
    requester_id,
    preferences,
    limit,
    cursor=None,
    requester_location=None,
    excluded_user_ids=None,
    today=None,
):
    """Fetch one deterministic discovery page plus look-ahead."""

    if excluded_user_ids is None:
        excluded_user_ids = (
            get_discovery_excluded_user_ids(
                requester_id
            )
        )

    query = (
        build_discovery_profile_query(
            requester_id=requester_id,
            preferences=preferences,
            excluded_user_ids=(
                excluded_user_ids
            ),
            requester_location=(
                requester_location
            ),
            cursor=cursor,
            today=today,
        )
    )

    projection = {
        field: 1
        for field in (
            DISCOVERY_PROFILE_SOURCE_FIELDS
        )
    }

    cursor_result = (
        mongo.db.profiles.find(
            query,
            projection,
        )
        .sort(
            [
                (
                    "last_active_at",
                    DESCENDING,
                ),
                (
                    "_id",
                    DESCENDING,
                ),
            ]
        )
        .limit(
            int(limit)
            + 1
        )
    )

    documents = list(
        cursor_result
    )

    has_more = (
        len(documents)
        > int(limit)
    )

    if has_more:
        documents = documents[
            : int(limit)
        ]

    return {
        "profiles": documents,
        "has_more": has_more,
    }


__all__ = [
    "build_discovery_profile_query",
    "find_discovery_candidate_profiles",
    "get_discovery_excluded_user_ids",
]

