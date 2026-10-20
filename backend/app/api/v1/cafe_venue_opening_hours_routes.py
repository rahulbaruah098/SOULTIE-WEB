"""Authenticated, transaction-protected cafe opening-hours API."""

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.schemas.cafe_venue_operations import CafeVenueOperationsError
from app.schemas.venue_opening_hours import VenueOpeningHoursError
from app.repositories.venue_hours_reservation_repository import (
    VenueHoursSnapshotError,
)
from app.repositories.cafe_reservation_fence_repository import (
    CafeReservationFenceError,
)
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)
from app.services.cafe_reservation_transaction_service import (
    CafeReservationTransactionError,
)
from app.services.venue_hours_booking_fence_adapter import (
    VenueHoursFenceError,
    acquire_hours_booking_fence,
)
from app.services.venue_opening_hours_guard_service import (
    VenueHoursBookingConflict,
)
from app.services.venue_opening_hours_transaction_service import (
    VenueHoursUpdateError,
    update_venue_opening_hours_atomically,
)

cafe_venue_opening_hours_bp = Blueprint(
    "cafe_venue_opening_hours",
    __name__,
    url_prefix="/cafe-partners",
)


def _response(payload, status):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _bearer_token():
    header = request.headers.get("Authorization", "")
    if (
        not isinstance(header, str)
        or not header.startswith("Bearer ")
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    token = header[7:]

    if (
        not 47 <= len(token) <= 300
        or any(character.isspace() for character in token)
        or "," in token
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return token


@cafe_venue_opening_hours_bp.patch("/venue/opening-hours")
def update_cafe_venue_opening_hours_api():
    try:
        context = authenticate_cafe_session(
            token=_bearer_token()
        )

        if not isinstance(context, dict):
            raise CafeSessionInvalid("Invalid cafe session.")

        partner_id = context.get("partner_id")
        venue_id = context.get("venue_id")

        if (
            context.get("account_type") != "cafe_partner"
            or not isinstance(partner_id, str)
            or not ObjectId.is_valid(partner_id)
            or not isinstance(venue_id, str)
            or not ObjectId.is_valid(venue_id)
        ):
            raise CafeSessionInvalid("Invalid cafe session.")

    except CafeSessionInvalid:
        return _response(
            {"error": "Cafe access denied."}, 401
        )

    except PyMongoError:
        current_app.logger.exception(
            "Cafe session lookup unavailable"
        )
        return _response(
            {"error": "Opening hours temporarily unavailable."},
            503,
        )

    if (
        request.content_length is not None
        and request.content_length > 4096
    ):
        return _response(
            {"error": "Opening-hours request too large."},
            413,
        )

    if not request.is_json:
        return _response(
            {"error": "JSON request required."}, 415
        )

    if len(request.get_data(cache=True)) > 4096:
        return _response(
            {"error": "Opening-hours request too large."},
            413,
        )

    payload = request.get_json(silent=True)

    if (
        not isinstance(payload, dict)
        or set(payload) != {"opening_hours"}
    ):
        return _response(
            {"error": "Invalid opening-hours request."},
            400,
        )

    try:
        result = update_venue_opening_hours_atomically(
            venue_id=venue_id,
            partner_id=partner_id,
            proposed_hours=payload["opening_hours"],
            acquire_booking_fence=acquire_hours_booking_fence,
        )

    except (VenueOpeningHoursError, CafeVenueOperationsError):
        return _response(
            {"error": "Invalid opening hours."}, 400
        )

    except VenueHoursBookingConflict:
        return _response(
            {"error": "Existing reservations conflict with these hours."},
            409,
        )

    except VenueHoursSnapshotError:
        return _response(
            {"error": "Reservation compatibility could not be verified."},
            409,
        )

    except VenueHoursUpdateError:
        return _response(
            {"error": "Approved cafe venue access denied."},
            403,
        )

    except (
        VenueHoursFenceError,
        CafeReservationFenceError,
        CafeReservationTransactionError,
        PyMongoError,
    ):
        current_app.logger.exception(
            "Cafe opening-hours transaction unavailable"
        )
        return _response(
            {"error": "Opening hours temporarily unavailable."},
            503,
        )

    if (
        not isinstance(result, dict)
        or result.get("venue_id") != venue_id
        or result.get("status") != "approved"
        or not isinstance(result.get("opening_hours"), dict)
        or not isinstance(result.get("weekly_hours"), dict)
        or result["opening_hours"].get("weekly")
        != result["weekly_hours"]
    ):
        current_app.logger.error(
            "Invalid opening-hours update acknowledgement"
        )
        return _response(
            {"error": "Opening hours temporarily unavailable."},
            503,
        )

    return _response(
        {
            "venue_id": venue_id,
            "status": "approved",
            "opening_hours": result["opening_hours"],
            "message": "Cafe opening hours updated.",
        },
        200,
    )
