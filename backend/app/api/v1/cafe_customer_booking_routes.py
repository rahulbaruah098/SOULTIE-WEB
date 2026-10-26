"""Authenticated Soultie dating-user cafe reservation creation.

Booking policy, current eligibility, venue fence, capacity and
outbox writes are enforced by the existing transactional service.
"""

from bson import ObjectId
from flask import Blueprint, g, jsonify, request, current_app
from pymongo.errors import PyMongoError

from app.security.authentication import auth_required
from app.schemas.reservation import (
    ReservationValidationError,
    RESERVATION_STATUSES,
    validate_reservation_request,
)
from app.services.booking_service import (
    CafeBookingError,
    CafeBookingCapacityConflict,
    create_pending_cafe_booking_with_live_access,
)
from app.services.cafe_booking_policy_service import (
    CafeBookingPolicyError,
)
from app.security.cafe_booking_customer_access import (
    CafeBookingCustomerAccessError,
)

cafe_customer_booking_bp = Blueprint(
    "cafe_customer_booking",
    __name__,
    url_prefix="/venues",
)


def _response(body, status):
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@cafe_customer_booking_bp.post("/bookings")
@auth_required
def create_customer_cafe_booking_api():
    """Create a pending booking for the authenticated dating user."""
    current = getattr(g, "current_user", None)

    if not isinstance(current, dict):
        return _response({"error": "Customer authentication required."}, 401)

    customer_oid = current.get("_id")
    if not isinstance(customer_oid, ObjectId):
        return _response({"error": "Invalid customer identity."}, 401)

    if (
        request.content_length is not None
        and request.content_length > 4096
    ):
        return _response({"error": "Booking request too large."}, 413)

    if not request.is_json:
        return _response({"error": "JSON request required."}, 415)

    if len(request.get_data(cache=True)) > 4096:
        return _response({"error": "Booking request too large."}, 413)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _response({"error": "Invalid booking request."}, 400)

    try:
        cleaned = validate_reservation_request(body)
    except ReservationValidationError:
        return _response({"error": "Invalid booking request."}, 400)

    # Never accept customer identity or trusted callbacks from HTTP.
    try:
        result = create_pending_cafe_booking_with_live_access(
            customer_id=str(customer_oid),
            request_data=body,
        )
    except CafeBookingCapacityConflict:
        return _response({"error": "Booking capacity unavailable."}, 409)
    except CafeBookingPolicyError:
        return _response({"error": "Requested booking unavailable."}, 409)
    except CafeBookingCustomerAccessError:
        return _response({"error": "Customer not eligible for booking."}, 403)
    except CafeBookingError:
        return _response({"error": "Booking could not be accepted."}, 409)
    except ValueError:
        return _response({"error": "Booking could not be accepted."}, 409)
    except PyMongoError:
        current_app.logger.exception("Customer cafe booking database failure")
        return _response({"error": "Booking temporarily unavailable."}, 503)

    if (
        not isinstance(result, dict)
        or not isinstance(result.get("reservation_id"), str)
        or not ObjectId.is_valid(result["reservation_id"])
        or result.get("status") not in RESERVATION_STATUSES
        or type(result.get("replayed")) is not bool
        or (
            result.get("venue_id") is not None
            and result["venue_id"] != cleaned["venue_id"]
        )
    ):
        current_app.logger.error("Invalid customer booking acknowledgement")
        return _response({"error": "Booking temporarily unavailable."}, 503)

    replayed = result["replayed"]

    return _response({
        "reservation_id": result["reservation_id"],
        "venue_id": cleaned["venue_id"],
        "status": result["status"],
        "replayed": replayed,
        "message": (
            "Existing booking returned."
            if replayed
            else "Booking request submitted."
        ),
    }, 200 if replayed else 201)
