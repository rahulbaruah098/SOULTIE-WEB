"""Read-only, public-field venue discovery persistence.

Only approved venues are visible. No partner credentials,
private owner details, moderation notes, or internal booking
configuration can be returned through this projection.
"""

import re

from bson import ObjectId
from app.extensions import mongo


class VenueDiscoveryError(ValueError):
    """Invalid discovery query or pagination."""


PUBLIC_VENUE_FIELDS = {
    "_id": 1,
    "venue_name": 1,
    "description": 1,
    "address": 1,
    "city": 1,
    "state": 1,
    "postal_code": 1,
    "website": 1,
    "facilities": 1,
    "location": 1,
    "location_source": 1,
    "status": 1,
}


def _clean_text(value, *, field, maximum=80):
    if value is None:
        return None
    if not isinstance(value, str):
        raise VenueDiscoveryError(f"Invalid {field}.")
    value = value.strip()
    if not value or len(value) > maximum:
        raise VenueDiscoveryError(f"Invalid {field}.")
    if any(ord(character) < 32 or ord(character) == 127
           for character in value):
        raise VenueDiscoveryError(f"Invalid {field}.")
    return value


def _page_limit(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise VenueDiscoveryError("Invalid page limit.")
    if not 1 <= value <= 50:
        raise VenueDiscoveryError("Page limit must be 1 to 50.")
    return value


SUPPORTED_FACILITIES = frozenset({
    "wifi",
    "parking",
    "outdoor_seating",
    "indoor_seating",
    "wheelchair_accessible",
    "air_conditioning",
    "restroom",
    "power_outlets",
    "private_seating",
    "group_seating",
    "pet_friendly",
    "vegetarian_options",
    "vegan_options",
    "halal_options",
    "live_music",
    "quiet_area",
})


def _facility_filter(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise VenueDiscoveryError("Invalid facilities.")
    names = [item.strip().lower() for item in value.split(",")]
    if (
        not names
        or len(names) > 16
        or any(name not in SUPPORTED_FACILITIES for name in names)
        or len(names) != len(set(names))
    ):
        raise VenueDiscoveryError("Invalid facilities.")
    return names


def search_approved_venues(
    *,
    name=None,
    city=None,
    state=None,
    facilities=None,
    limit=20,
    after_id=None,
):
    """Return up to limit+1 public documents for cursor pagination.

    The caller owns result serialization and next-cursor generation.
    No user-provided MongoDB operators are accepted.
    """
    limit = _page_limit(limit)
    name = _clean_text(name, field="name")
    city = _clean_text(city, field="city", maximum=100)
    state = _clean_text(state, field="state", maximum=100)
    facilities = _facility_filter(facilities)

    query = {"status": "approved"}

    if name is not None:
        query["venue_name"] = {
            "$regex": re.escape(name),
            "$options": "i",
        }

    if city is not None:
        query["city"] = {
            "$regex": "^" + re.escape(city) + "$",
            "$options": "i",
        }

    if state is not None:
        query["state"] = {
            "$regex": "^" + re.escape(state) + "$",
            "$options": "i",
        }

    if facilities is not None:
        query["facilities"] = {"$all": facilities}

    if after_id is not None:
        if not isinstance(after_id, str) or not ObjectId.is_valid(after_id):
            raise VenueDiscoveryError("Invalid pagination cursor.")
        query["_id"] = {"$gt": ObjectId(after_id)}

    cursor = (
        mongo.db.venues
        .find(query, PUBLIC_VENUE_FIELDS)
        .sort("_id", 1)
        .limit(limit + 1)
    )
    return list(cursor)

def get_public_approved_venue(venue_id):
    """Return a single approved venue with public fields only."""
    if (
        not isinstance(venue_id, str)
        or len(venue_id) != 24
        or not ObjectId.is_valid(venue_id)
    ):
        raise VenueDiscoveryError("Invalid venue identifier.")

    return mongo.db.venues.find_one(
        {
            "_id": ObjectId(venue_id),
            "status": "approved",
        },
        PUBLIC_VENUE_FIELDS,
    )

def search_nearby_approved_venues(
    *,
    latitude,
    longitude,
    radius_km=10,
    limit=20,
):
    """Find approved cafes near supplied coordinates.

    Requires a MongoDB 2dsphere index on venues.location.
    Only records with valid indexed GeoJSON Points participate.
    """
    from app.schemas.venue_location import (
        validate_venue_coordinates,
    )

    coordinates = validate_venue_coordinates({
        "latitude": latitude,
        "longitude": longitude,
    })

    if (
        isinstance(radius_km, bool)
        or not isinstance(radius_km, (int, float))
        or not 1 <= radius_km <= 50
    ):
        raise VenueDiscoveryError(
            "Radius must be between 1 and 50 km."
        )

    limit = _page_limit(limit)

    query = {
        "status": "approved",
        "location": {
            "$near": {
                "$geometry": {
                    "type": "Point",
                    "coordinates": [
                        coordinates["longitude"],
                        coordinates["latitude"],
                    ],
                },
                "$maxDistance": float(radius_km) * 1000,
            },
        },
    }

    return list(
        mongo.db.venues.find(query, PUBLIC_VENUE_FIELDS)
        .limit(limit)
    )
