"""Validated geographical coordinates for Soultie venues.

MongoDB GeoJSON stores coordinates as [longitude, latitude].
No coordinates are inferred from an address or city name.
"""

import math


class VenueLocationError(ValueError):
    """Invalid geographical location data."""


def _coordinate(value, *, field, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise VenueLocationError(f"Invalid {field}.")
    return float(value)


def validate_venue_coordinates(data):
    """Validate an explicit latitude/longitude input object.

    Both coordinates must be supplied together. Unknown keys
    and MongoDB-style operators are rejected.
    """
    if not isinstance(data, dict):
        raise VenueLocationError("Expected coordinate object.")

    if set(data) != {"latitude", "longitude"}:
        raise VenueLocationError(
            "Both latitude and longitude are required."
        )

    latitude = _coordinate(
        data["latitude"],
        field="latitude",
        minimum=-90,
        maximum=90,
    )
    longitude = _coordinate(
        data["longitude"],
        field="longitude",
        minimum=-180,
        maximum=180,
    )

    return {
        "latitude": latitude,
        "longitude": longitude,
    }


def build_venue_geojson(data):
    """Create the MongoDB GeoJSON Point representation."""
    clean = validate_venue_coordinates(data)
    return {
        "type": "Point",
        "coordinates": [
            clean["longitude"],
            clean["latitude"],
        ],
    }


def public_venue_coordinates(geojson):
    """Return a safely validated public coordinate object.

    Missing coordinates remain absent. Invalid stored data
    must not be exposed as a valid map point.
    """
    if geojson is None:
        return None

    if (
        not isinstance(geojson, dict)
        or geojson.get("type") != "Point"
        or set(geojson) != {"type", "coordinates"}
    ):
        raise VenueLocationError("Invalid stored venue location.")

    coordinates = geojson.get("coordinates")

    if (
        not isinstance(coordinates, list)
        or len(coordinates) != 2
    ):
        raise VenueLocationError("Invalid stored venue location.")

    longitude, latitude = coordinates

    return validate_venue_coordinates({
        "latitude": latitude,
        "longitude": longitude,
    })
