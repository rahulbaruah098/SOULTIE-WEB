"""Approved venue discovery API for the Soultie mobile client."""

import re

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.schemas.venue_location import (
    VenueLocationError,
    public_venue_coordinates,
)
from app.repositories.venue_discovery_repository import (
    VenueDiscoveryError,
    get_public_approved_venue,
    search_nearby_approved_venues,
    search_approved_venues,
)

venue_discovery_bp = Blueprint(
    "venue_discovery",
    __name__,
    url_prefix="/venues",
)

ALLOWED_QUERY_KEYS = frozenset({
    "name", "city", "state", "facilities", "limit", "after",
})

PUBLIC_FIELDS = (
    "venue_name",
    "description",
    "address",
    "city",
    "state",
    "postal_code",
    "website",
    "facilities",
)


def _response(payload, status):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _venue_response(document):
    if (
        not isinstance(document, dict)
        or not isinstance(document.get("_id"), ObjectId)
        or document.get("status") != "approved"
    ):
        raise ValueError("Invalid approved venue document.")

    result = {"venue_id": str(document["_id"])}

    for field in PUBLIC_FIELDS:
        value = document.get(field)

        if field == "facilities":
            result[field] = (
                [item for item in value if isinstance(item, str)]
                if isinstance(value, list)
                else []
            )
        elif isinstance(value, str):
            result[field] = value

    if document.get("location") is not None:
        try:
            coordinates = public_venue_coordinates(
                document["location"]
            )
        except VenueLocationError:
            current_app.logger.warning(
                "Ignoring invalid public venue coordinates"
            )
        else:
            result["location"] = coordinates
            if document.get("location_source") == "cafe_partner":
                result["location_source"] = "cafe_partner"

    return result


def _filters():
    if set(request.args) - ALLOWED_QUERY_KEYS:
        raise VenueDiscoveryError("Unknown query parameter.")

    for key in request.args:
        if len(request.args.getlist(key)) != 1:
            raise VenueDiscoveryError("Repeated query parameter.")

    raw_limit = request.args.get("limit", "20")

    if not re.fullmatch(r"[1-9][0-9]{0,1}", raw_limit):
        raise VenueDiscoveryError("Invalid page limit.")

    limit = int(raw_limit)

    if not 1 <= limit <= 50:
        raise VenueDiscoveryError("Invalid page limit.")

    after = request.args.get("after")

    if after is not None and (
        not ObjectId.is_valid(after)
        or len(after) != 24
    ):
        raise VenueDiscoveryError("Invalid pagination cursor.")

    return {
        "name": request.args.get("name"),
        "city": request.args.get("city"),
        "state": request.args.get("state"),
        "facilities": request.args.get("facilities"),
        "limit": limit,
        "after_id": after,
    }


@venue_discovery_bp.get("")
@venue_discovery_bp.get("/")
def discover_venues_api():
    """Return approved public venues, sorted by stable _id cursor."""
    try:
        filters = _filters()
        documents = search_approved_venues(**filters)

        if not isinstance(documents, list):
            raise ValueError("Invalid discovery result.")

        limit = filters["limit"]
        if len(documents) > limit + 1:
            raise ValueError("Invalid result page size.")

        has_more = len(documents) > limit
        selected = documents[:limit]
        venues = [_venue_response(item) for item in selected]

        next_cursor = (
            str(selected[-1]["_id"])
            if has_more and selected
            else None
        )

        return _response({
            "venues": venues,
            "pagination": {
                "limit": limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        }, 200)

    except VenueDiscoveryError:
        return _response({
            "error": "Invalid venue discovery parameters."
        }, 400)

    except PyMongoError:
        current_app.logger.exception(
            "Venue discovery database unavailable"
        )
        return _response({
            "error": "Venue discovery temporarily unavailable."
        }, 503)

    except (ValueError, TypeError, KeyError):
        current_app.logger.error(
            "Venue discovery returned invalid result data"
        )
        return _response({
            "error": "Venue discovery temporarily unavailable."
        }, 503)

@venue_discovery_bp.get("/<venue_id>")
def get_public_venue_detail_api(venue_id):
    """Retrieve an approved venue without exposing partner data."""
    try:
        if (
            not isinstance(venue_id, str)
            or len(venue_id) != 24
            or not ObjectId.is_valid(venue_id)
        ):
            return _response({
                "error": "Invalid venue identifier."
            }, 400)

        venue = get_public_approved_venue(venue_id)

        if venue is None:
            return _response({
                "error": "Venue not found."
            }, 404)

        return _response({
            "venue": _venue_response(venue)
        }, 200)

    except VenueDiscoveryError:
        return _response({
            "error": "Invalid venue identifier."
        }, 400)

    except PyMongoError:
        current_app.logger.exception(
            "Venue detail database unavailable"
        )
        return _response({
            "error": "Venue details temporarily unavailable."
        }, 503)

    except (ValueError, TypeError, KeyError):
        current_app.logger.error(
            "Venue detail returned invalid data"
        )
        return _response({
            "error": "Venue details temporarily unavailable."
        }, 503)

@venue_discovery_bp.get("/nearby")
def nearby_venues_api():
    """Search approved venues near a user-supplied map location."""
    allowed = frozenset({
        "latitude", "longitude", "radius_km", "limit",
    })

    try:
        if set(request.args) != (
            set(request.args) & allowed
        ):
            raise VenueDiscoveryError("Unknown parameter.")

        for key in request.args:
            if len(request.args.getlist(key)) != 1:
                raise VenueDiscoveryError("Repeated parameter.")

        if not {"latitude", "longitude"}.issubset(request.args):
            raise VenueDiscoveryError("Coordinates required.")

        def numeric(key, default=None):
            raw = request.args.get(key, default)
            if (
                not isinstance(raw, str)
                or len(raw) > 24
                or re.fullmatch(
                    r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)",
                    raw,
                ) is None
            ):
                raise VenueDiscoveryError("Invalid numeric input.")
            return float(raw)

        latitude = numeric("latitude")
        longitude = numeric("longitude")
        radius_km = numeric("radius_km", "10")

        raw_limit = request.args.get("limit", "20")
        if re.fullmatch(r"[1-9][0-9]{0,1}", raw_limit) is None:
            raise VenueDiscoveryError("Invalid limit.")

        limit = int(raw_limit)

        if not 1 <= limit <= 50:
            raise VenueDiscoveryError("Invalid limit.")

        documents = search_nearby_approved_venues(
            latitude=latitude,
            longitude=longitude,
            radius_km=radius_km,
            limit=limit,
        )

        if (
            not isinstance(documents, list)
            or len(documents) > limit
        ):
            raise ValueError("Invalid nearby result.")

        return _response({
            "venues": [_venue_response(doc) for doc in documents],
            "search": {
                "latitude": latitude,
                "longitude": longitude,
                "radius_km": radius_km,
                "limit": limit,
            },
        }, 200)

    except (VenueDiscoveryError, VenueLocationError, OverflowError):
        return _response({
            "error": "Invalid nearby venue search parameters."
        }, 400)

    except PyMongoError:
        current_app.logger.exception(
            "Nearby venue discovery database unavailable"
        )
        return _response({
            "error": "Nearby venue discovery temporarily unavailable."
        }, 503)

    except (ValueError, TypeError, KeyError):
        current_app.logger.error(
            "Nearby venue discovery returned invalid data"
        )
        return _response({
            "error": "Nearby venue discovery temporarily unavailable."
        }, 503)
