"""Public read-only, advisory cafe reservation availability API."""

from datetime import date

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.services.cafe_live_availability_service import (
    CafeLiveAvailabilityError,
    read_live_cafe_availability,
)

cafe_customer_availability_bp = Blueprint(
    "cafe_customer_availability",
    __name__,
    url_prefix="/venues",
)


def _response(data, code):
    response = jsonify(data)
    response.status_code = code
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@cafe_customer_availability_bp.get("/<venue_id>/availability")
def get_cafe_customer_availability_api(venue_id):
    """Show possible times, never a reservation guarantee."""
    if not isinstance(venue_id, str) or (
        len(venue_id) != 24 or not ObjectId.is_valid(venue_id)
    ):
        return _response({"error": "Invalid venue."}, 400)

    if set(request.args.keys()) != {"date", "party_size"}:
        return _response(
            {"error": "Date and party_size are required."}, 400
        )

    if (
        len(request.args.getlist("date")) != 1
        or len(request.args.getlist("party_size")) != 1
    ):
        return _response({"error": "Duplicate parameters."}, 400)

    day_text = request.args.get("date", "")
    party_text = request.args.get("party_size", "")

    if (
        len(day_text) != 10
        or day_text[4:5] != "-"
        or day_text[7:8] != "-"
        or not (
            day_text[:4].isdigit()
            and day_text[5:7].isdigit()
            and day_text[8:10].isdigit()
        )
    ):
        return _response({"error": "Invalid date."}, 400)

    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        return _response({"error": "Invalid date."}, 400)

    if (
        not party_text.isascii()
        or not party_text.isdecimal()
        or not 1 <= len(party_text) <= 3
        or party_text.startswith("0")
    ):
        return _response({"error": "Invalid party size."}, 400)

    party_size = int(party_text)
    if not 1 <= party_size <= 100:
        return _response({"error": "Invalid party size."}, 400)

    try:
        result = read_live_cafe_availability(
            venue_id=venue_id,
            day=day,
            party_size=party_size,
        )
    except CafeLiveAvailabilityError:
        return _response(
            {"error": "Venue availability is unavailable."}, 404
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe availability database request failed"
        )
        return _response(
            {"error": "Availability temporarily unavailable."}, 503
        )

    if (
        not isinstance(result, dict)
        or result.get("venue_id") != venue_id
        or result.get("date") != day_text
        or result.get("party_size") != party_size
        or result.get("advisory") is not True
        or not isinstance(result.get("slots"), list)
        or not isinstance(result.get("timezone"), str)
    ):
        current_app.logger.error(
            "Invalid cafe availability service response"
        )
        return _response(
            {"error": "Availability temporarily unavailable."}, 503
        )

    return _response(result, 200)
