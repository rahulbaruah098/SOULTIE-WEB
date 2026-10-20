"""Authenticated customer-owned cafe booking history."""



from datetime import datetime, timezone



from bson import ObjectId

from flask import Blueprint, g, jsonify, request, current_app

from pymongo.errors import PyMongoError



from app.extensions import mongo

from app.security.authentication import auth_required

from app.schemas.reservation import RESERVATION_STATUSES





cafe_customer_booking_history_bp = Blueprint(

    "cafe_customer_booking_history",

    __name__,

    url_prefix="/venues",

)





def _response(payload, status=200):

    result = jsonify(payload)

    result.status_code = status

    result.headers["Cache-Control"] = "no-store"

    result.headers["Pragma"] = "no-cache"

    result.headers["X-Content-Type-Options"] = "nosniff"

    return result





def _utc_iso(value):

    if not isinstance(value, datetime):

        return None

    if value.tzinfo is None:

        value = value.replace(tzinfo=timezone.utc)

    if value.utcoffset() is None:

        return None

    return value.astimezone(timezone.utc).isoformat()





@cafe_customer_booking_history_bp.get("/bookings/me")

@auth_required

def list_my_cafe_bookings():

    current = getattr(g, "current_user", None)



    if (

        not isinstance(current, dict)

        or not isinstance(current.get("_id"), ObjectId)

    ):

        return _response({"error": "Customer access denied."}, 401)



    if set(request.args.keys()) - {"status", "limit"}:

        return _response({"error": "Invalid query parameters."}, 400)



    if any(

        len(request.args.getlist(key)) != 1

        for key in request.args.keys()

    ):

        return _response({"error": "Duplicate parameters."}, 400)



    status = request.args.get("status", "all")

    if status != "all" and status not in RESERVATION_STATUSES:

        return _response({"error": "Invalid booking status."}, 400)



    raw_limit = request.args.get("limit", "20")

    if (

        not raw_limit.isascii()

        or not raw_limit.isdecimal()

        or not 1 <= len(raw_limit) <= 2

        or raw_limit.startswith("0")

    ):

        return _response({"error": "Invalid booking limit."}, 400)



    limit = int(raw_limit)

    if not 1 <= limit <= 50:

        return _response({"error": "Invalid booking limit."}, 400)



    customer_oid = current["_id"]

    query = {"customer_id": customer_oid}



    if status != "all":

        query["status"] = status



    projection = {

        "_id": 1,

        "venue_id": 1,

        "customer_id": 1,

        "status": 1,

        "start_at": 1,

        "end_at": 1,

        "party_size": 1,

        "created_at": 1,

        "updated_at": 1,

    }



    try:

        records = list(

            mongo.db.reservations.find(query, projection)

            .sort([("start_at", -1), ("_id", -1)])

            .limit(limit)

        )



        bookings = []

        for item in records:

            if (

                not isinstance(item, dict)

                or item.get("customer_id") != customer_oid

                or not isinstance(item.get("_id"), ObjectId)

                or not isinstance(item.get("venue_id"), ObjectId)

                or item.get("status") not in RESERVATION_STATUSES

            ):

                current_app.logger.error(

                    "Customer booking isolation or data failure"

                )

                return _response(

                    {"error": "Booking history unavailable."}, 503

                )



            bookings.append({

                "reservation_id": str(item["_id"]),

                "venue_id": str(item["venue_id"]),

                "status": item["status"],

                "start_at": _utc_iso(item.get("start_at")),

                "end_at": _utc_iso(item.get("end_at")),

                "party_size": item.get("party_size"),

                "created_at": _utc_iso(item.get("created_at")),

                "updated_at": _utc_iso(item.get("updated_at")),

            })



        return _response({

            "bookings": bookings,

            "count": len(bookings),

            "limit": limit,

            "status_filter": status,

        })



    except PyMongoError:

        current_app.logger.exception(

            "Customer booking history database failure"

        )

        return _response(

            {"error": "Booking history temporarily unavailable."},

            503,

        )


@cafe_customer_booking_history_bp.get("/bookings/me/<reservation_id>")
@auth_required
def get_my_cafe_booking_details(reservation_id):
    """Retrieve a single booking belonging to the logged-in customer."""
    current = getattr(g, "current_user", None)

    if (
        not isinstance(current, dict)
        or not isinstance(current.get("_id"), ObjectId)
    ):
        return _response({"error": "Customer access denied."}, 401)

    if (
        not isinstance(reservation_id, str)
        or len(reservation_id) != 24
        or not ObjectId.is_valid(reservation_id)
    ):
        return _response({"error": "Invalid reservation ID."}, 400)

    if request.args:
        return _response({"error": "Unexpected parameters."}, 400)

    reservation_oid = ObjectId(reservation_id)
    customer_oid = current["_id"]

    projection = {
        "_id": 1,
        "venue_id": 1,
        "customer_id": 1,
        "status": 1,
        "start_at": 1,
        "end_at": 1,
        "party_size": 1,
        "notes": 1,
        "created_at": 1,
        "updated_at": 1,
    }

    try:
        record = mongo.db.reservations.find_one(
            {
                "_id": reservation_oid,
                "customer_id": customer_oid,
            },
            projection,
        )

        if record is None:
            return _response({"error": "Booking not found."}, 404)

        if (
            not isinstance(record, dict)
            or record.get("_id") != reservation_oid
            or record.get("customer_id") != customer_oid
            or not isinstance(record.get("venue_id"), ObjectId)
            or record.get("status") not in RESERVATION_STATUSES
            or type(record.get("party_size")) is not int
            or not 1 <= record["party_size"] <= 100
            or not isinstance(record.get("notes", ""), str)
        ):
            current_app.logger.error("Invalid customer booking details")
            return _response(
                {"error": "Booking details unavailable."}, 503
            )

        start_text = _utc_iso(record.get("start_at"))
        end_text = _utc_iso(record.get("end_at"))

        if (
            not start_text
            or not end_text
            or datetime.fromisoformat(end_text)
            <= datetime.fromisoformat(start_text)
        ):
            return _response(
                {"error": "Booking details unavailable."}, 503
            )

        return _response({
            "booking": {
                "reservation_id": str(record["_id"]),
                "venue_id": str(record["venue_id"]),
                "status": record["status"],
                "start_at": start_text,
                "end_at": end_text,
                "party_size": record["party_size"],
                "notes": record.get("notes", ""),
                "created_at": _utc_iso(record.get("created_at")),
                "updated_at": _utc_iso(record.get("updated_at")),
            }
        })

    except PyMongoError:
        current_app.logger.exception(
            "Customer booking detail database failure"
        )
        return _response(
            {"error": "Booking details temporarily unavailable."},
            503,
        )


@cafe_customer_booking_history_bp.post(
    "/bookings/me/<reservation_id>/cancel"
)
@auth_required
def cancel_my_cafe_booking(reservation_id):
    """Cancel only the authenticated customer's reservation."""
    from app.services.cafe_reservation_lifecycle_service import (
        change_cafe_reservation_status,
        CafeReservationLifecycleServiceDenied,
    )
    from app.repositories.cafe_reservation_lifecycle_repository import (
        CafeReservationLifecycleDenied,
        CafeReservationLifecycleConflict,
    )

    current = getattr(g, "current_user", None)

    if (
        not isinstance(current, dict)
        or not isinstance(current.get("_id"), ObjectId)
    ):
        return _response(
            {"error": "Customer authentication required."}, 401
        )

    if (
        not isinstance(reservation_id, str)
        or len(reservation_id) != 24
        or not ObjectId.is_valid(reservation_id)
    ):
        return _response(
            {"error": "Invalid reservation ID."}, 400
        )

    if request.args:
        return _response(
            {"error": "Unexpected query parameters."}, 400
        )

    # Cancellation has no client-controlled actor, status or venue.
    # In particular, do not accept JSON-supplied identity or role.
    if request.content_length not in (None, 0):
        return _response(
            {"error": "Cancellation request must have no body."},
            400,
        )

    if request.get_data(cache=True):
        return _response(
            {"error": "Cancellation request must have no body."},
            400,
        )

    try:
        result = change_cafe_reservation_status(
            reservation_id=reservation_id,
            actor_id=str(current["_id"]),
            actor_role="customer",
            decision="cancelled",
        )

    except (
        CafeReservationLifecycleServiceDenied,
        CafeReservationLifecycleDenied,
    ):
        # Do not reveal whether another customer's booking exists.
        return _response(
            {"error": "Booking not found or unavailable."}, 404
        )

    except CafeReservationLifecycleConflict:
        return _response(
            {"error": "This booking cannot be cancelled."}, 409
        )

    if (
        not isinstance(result, dict)
        or result.get("reservation_id") != reservation_id
        or result.get("customer_id") != str(current["_id"])
        or result.get("status") != "cancelled"
        or not isinstance(result.get("venue_id"), str)
    ):
        current_app.logger.error(
            "Customer cancellation result integrity failure"
        )
        return _response(
            {"error": "Cancellation result unavailable."}, 503
        )

    return _response({
        "reservation_id": result["reservation_id"],
        "venue_id": result["venue_id"],
        "previous_status": result.get("previous_status"),
        "status": "cancelled",
    })
