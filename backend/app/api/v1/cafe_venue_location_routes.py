"""Authenticated, approved-owner venue location management."""

from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.schemas.venue_location import (
    VenueLocationError,
    build_venue_geojson,
    public_venue_coordinates,
)
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)

cafe_venue_location_bp = Blueprint(
    "cafe_venue_location",
    __name__,
    url_prefix="/cafe-partners",
)


def _response(payload, status):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _bearer():
    authorization = request.headers.get("Authorization", "")
    if (
        not isinstance(authorization, str)
        or not authorization.startswith("Bearer ")
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    token = authorization[7:]
    if (
        not 47 <= len(token) <= 300
        or any(ch.isspace() for ch in token)
        or "," in token
    ):
        raise CafeSessionInvalid("Invalid cafe session.")
    return token


@cafe_venue_location_bp.patch("/venue/location")
def update_cafe_venue_location_api():
    """Save validated coordinates for the authenticated approved venue."""
    try:
        token = _bearer()
        context = authenticate_cafe_session(token=token)

        if not isinstance(context, dict) or (
            context.get("account_type") != "cafe_partner"
        ):
            raise CafeSessionInvalid("Invalid cafe session.")

        partner_id = context.get("partner_id")
        venue_id = context.get("venue_id")

        if (
            not isinstance(partner_id, str)
            or not ObjectId.is_valid(partner_id)
            or not isinstance(venue_id, str)
            or not ObjectId.is_valid(venue_id)
        ):
            raise CafeSessionInvalid("Invalid cafe session.")

    except CafeSessionInvalid:
        return _response({"error": "Cafe access denied."}, 401)

    if request.content_length is not None and request.content_length > 1024:
        return _response({"error": "Location request too large."}, 413)

    if not request.is_json:
        return _response({"error": "JSON request required."}, 415)

    if len(request.get_data(cache=True)) > 1024:
        return _response({"error": "Location request too large."}, 413)

    try:
        payload = request.get_json(silent=True)
        point = build_venue_geojson(payload)
    except VenueLocationError:
        return _response({"error": "Invalid venue coordinates."}, 400)

    try:
        now = datetime.now(timezone.utc)
        updated = mongo.db.venues.find_one_and_update(
            {
                "_id": ObjectId(venue_id),
                "owner_id": partner_id,
                "status": "approved",
            },
            {
                "$set": {
                    "location": point,
                    "location_source": "cafe_partner",
                    "location_updated_at": now,
                    "updated_at": now,
                },
            },
            projection={
                "_id": 1,
                "status": 1,
                "location": 1,
            },
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe venue location database unavailable"
        )
        return _response({
            "error": "Venue location temporarily unavailable."
        }, 503)

    if updated is None:
        return _response({
            "error": "Approved venue not found or access denied."
        }, 404)

    try:
        if updated.get("status") != "approved":
            raise ValueError("Unexpected venue status")
        coordinates = public_venue_coordinates(updated.get("location"))
        if coordinates != public_venue_coordinates(point):
            raise ValueError("Unexpected saved location")
    except (VenueLocationError, ValueError):
        current_app.logger.error("Invalid venue location update result")
        return _response({
            "error": "Venue location temporarily unavailable."
        }, 503)

    return _response({
        "venue_id": venue_id,
        "status": "approved",
        "location": coordinates,
        "location_source": "cafe_partner",
        "message": "Venue coordinates updated.",
    }, 200)
