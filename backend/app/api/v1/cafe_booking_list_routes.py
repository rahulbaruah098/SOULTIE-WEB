"""Read-only, authenticated cafe booking management API."""

from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.schemas.reservation import RESERVATION_STATUSES
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)


cafe_booking_list_bp = Blueprint(
    "cafe_booking_list",
    __name__,
    url_prefix="/cafe-partners",
)


def _response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _serialize_time(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@cafe_booking_list_bp.get("/bookings")
def list_cafe_bookings():
    authorization = request.headers.get("Authorization", "")

    if (
        not isinstance(authorization, str)
        or not authorization.startswith("Bearer ")
    ):
        return _response({"error": "Cafe authentication required."}, 401)

    token = authorization[7:]
    if (
        not 47 <= len(token) <= 300
        or any(character.isspace() for character in token)
        or "," in token
    ):
        return _response({"error": "Invalid cafe session."}, 401)

    if "venue_id" in request.args:
        return _response(
            {"error": "Venue selection is not permitted."},
            400,
        )

    status = request.args.get("status", "all")
    if status != "all" and status not in RESERVATION_STATUSES:
        return _response({"error": "Invalid booking status."}, 400)

    raw_limit = request.args.get("limit", "20")
    if not raw_limit.isascii() or not raw_limit.isdecimal():
        return _response({"error": "Invalid booking limit."}, 400)

    limit = int(raw_limit)
    if not 1 <= limit <= 50:
        return _response({"error": "Invalid booking limit."}, 400)

    try:
        context = authenticate_cafe_session(token=token)

        if (
            not isinstance(context, dict)
            or context.get("account_type") != "cafe_partner"
        ):
            raise CafeSessionInvalid("Cafe session invalid.")

        venue_id = context.get("venue_id")
        partner_id = context.get("partner_id")

        if (
            not isinstance(venue_id, str)
            or not ObjectId.is_valid(venue_id)
            or not isinstance(partner_id, str)
            or not ObjectId.is_valid(partner_id)
        ):
            raise CafeSessionInvalid("Cafe identity invalid.")

        # The session service rechecks live partner approval
        # and venue ownership on every authenticated request.
        query = {"venue_id": ObjectId(venue_id)}

        if status != "all":
            query["status"] = status

        projection = {
            "_id": 1,
            "venue_id": 1,
            "status": 1,
            "start_at": 1,
            "party_size": 1,
            "created_at": 1,
            "updated_at": 1,
        }

        cursor = (
            mongo.db.reservations.find(query, projection)
            .sort([("start_at", -1), ("_id", -1)])
            .limit(limit)
        )

        records = list(cursor)

        bookings = []
        for item in records:
            if item.get("venue_id") != ObjectId(venue_id):
                current_app.logger.error(
                    "Cafe booking isolation mismatch"
                )
                return _response(
                    {"error": "Bookings temporarily unavailable."},
                    503,
                )

            bookings.append({
                "reservation_id": str(item["_id"]),
                "status": item.get("status"),
                "start_at": _serialize_time(item.get("start_at")),
                "party_size": item.get("party_size"),
                "created_at": _serialize_time(
                    item.get("created_at")
                ),
                "updated_at": _serialize_time(
                    item.get("updated_at")
                ),
            })

        return _response({
            "venue_id": venue_id,
            "bookings": bookings,
            "count": len(bookings),
            "limit": limit,
            "status_filter": status,
        })

    except CafeSessionInvalid:
        return _response({"error": "Cafe access denied."}, 401)

    except PyMongoError:
        current_app.logger.exception(
            "Cafe booking list database failure"
        )
        return _response(
            {"error": "Bookings temporarily unavailable."},
            503,
        )