"""Canonical Soultie profile and discovery data models."""

from copy import deepcopy
from datetime import datetime, timezone


MINIMUM_USER_AGE = 18

ONBOARDING_STATUSES = {
    "not_started",
    "in_progress",
    "completed",
}

ONBOARDING_STEPS = (
    "basics",
    "identity",
    "location",
    "culture",
    "about",
    "intentions",
    "preferences",
    "media",
    "review",
)

PROFILE_VISIBILITIES = {
    "visible",
    "hidden",
    "paused",
}

PROFILE_STATUSES = {
    "draft",
    "active",
    "paused",
    "disabled",
}

GENDER_IDENTITIES = {
    "man",
    "woman",
    "non_binary",
    "genderqueer",
    "self_described",
    "prefer_not_to_say",
}

RELATIONSHIP_INTENTIONS = {
    "long_term",
    "life_partner",
    "serious_dating",
    "casual_dating",
    "friendship",
    "figuring_it_out",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_profile_document(
    *,
    user_id,
    now=None,
):
    timestamp = now or utc_now()

    return {
        "user_id": user_id,

        # Lifecycle
        "profile_status": "draft",
        "visibility": "hidden",
        "onboarding_status": "not_started",

        "onboarding": {
            "current_step": "basics",
            "completed_steps": [],
            "started_at": None,
            "completed_at": None,
        },

        "profile_completion_percent": 0,

        # Core identity
        "display_name": None,
        "first_name": None,
        "last_name": None,

        # Stored separately for privacy-safe exact surname filtering.
        "surname_normalized": None,
        "surname_searchable": False,

        "birth_date": None,
        "gender_identity": None,
        "gender_custom_label": None,
        "pronouns": [],

        # Location
        # Precise/geospatial location is private server-side data and
        # must never be exposed directly in public profile responses.
        "current_city": {
            "city": None,
            "state": None,
            "country": "India",
        },

        "hometown": {
            "city": None,
            "state": None,
            "country": "India",
        },

        # GeoJSON location is intentionally absent until the user
        # explicitly grants location permission.
        # Example later:
        # "location": {
        #     "type": "Point",
        #     "coordinates": [longitude, latitude],
        # },

        # Culture-first, always user-declared.
        "culture": {
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },

        "languages": [],

        # About
        "bio": None,
        "interests": [],

        "prompts": [],

        "height_cm": None,

        "education": {
            "level": None,
            "institution": None,
            "field": None,
        },

        "work": {
            "title": None,
            "organization": None,
            "industry": None,
        },

        "lifestyle": {
            "smoking": None,
            "drinking": None,
            "diet": None,
            "fitness": None,
            "pets": None,
        },

        # Dating intent
        "relationship_intentions": [],

        # Media references only.
        # Actual files will live in object storage.
        "media": [],
        "primary_media_id": None,

        # Privacy controls.
        "privacy": {
            "show_age": True,
            "show_distance": True,
            "show_surname": False,
            "show_community": True,
            "show_hometown": True,
            "show_work": True,
            "show_education": True,
        },

        # Summary flags only; detailed verification records live
        # in the verification subsystem.
        "verification_summary": {
            "phone_verified": True,
            "email_verified": False,
            "selfie_verified": False,
            "identity_verified": False,
            "age_verified": False,
        },

        "last_active_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_discovery_preferences_document(
    *,
    user_id,
    now=None,
):
    timestamp = now or utc_now()

    return {
        "user_id": user_id,

        "interested_in": [],

        "age_min": MINIMUM_USER_AGE,
        "age_max": 99,

        "distance_enabled": True,
        "max_distance_km": 50,

        "relationship_intentions": [],

        # Optional culture filters are explicit choices only.
        "communities": [],
        "languages": [],

        # Exact normalized surname values only.
        # Never infer community/ethnicity from a surname.
        "surname_filter": {
            "mode": "none",
            "values": [],
        },

        "created_at": timestamp,
        "updated_at": timestamp,
    }


def clone_profile_template(
    *,
    user_id,
):
    return deepcopy(
        build_profile_document(
            user_id=user_id
        )
    )


def clone_discovery_preferences_template(
    *,
    user_id,
):
    return deepcopy(
        build_discovery_preferences_document(
            user_id=user_id
        )
    )
