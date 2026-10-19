"""Authenticated cafe booking lifecycle actions.

Reservation mutation and durable outbox persistence occur
inside the existing MongoDB transactional lifecycle service.

Never accept actor or venue identity from the HTTP body.
"""

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)
from app.services.cafe_reservation_lifecycle_service import (
    CafeReservationLifecycleServiceDenied,
    change_cafe_reservation_status,
)
from app.repositories.cafe_reservation_lifecycle_repository import (
    CafeReservationLifecycleDenied,
    CafeReservationLifecycleConflict,
)

cafe_booking_action_bp = Blueprint(
    "cafe_booking_action",
    __name__,
    url_prefix="/cafe-partners",
)

DECISIONS = frozenset({
    "confirmed",
    "rejected",
    "cancelled",
    "completed",
    "no_show",
})


def _response(payload, status):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _bearer_token():
    raw = request.headers.get("Authorization", "")

    if (
        not isinstance(raw, str)
        or not raw.startswith("Bearer ")
    ):
        raise CafeSessionInvalid("Cafe session required.")

    token = raw[7:]

    if (
        not 47 <= len(token) <= 300
        or "," in token
        or any(character.isspace() for character in token)
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return token


@cafe_booking_action_bp.post(
    "/bookings/<reservation_id>/decision"
)
def cafe_booking_decision_api(reservation_id):
    """Apply a cafe-owned reservation decision transactionally."""

    if not ObjectId.is_valid(reservation_id):
        return _response(
            {"error": "Invalid reservation ID."}, 400
        )

    try:
        token = _bearer_token()
        context = authenticate_cafe_session(token=token)

        if (
            not isinstance(context, dict)
            or context.get("account_type") != "cafe_partner"
        ):
            raise CafeSessionInvalid("Invalid cafe identity.")

        partner_id = context.get("partner_id")
        venue_id = context.get("venue_id")

        if (
            not isinstance(partner_id, str)
            or not ObjectId.is_valid(partner_id)
            or not isinstance(venue_id, str)
            or not ObjectId.is_valid(venue_id)
        ):
            raise CafeSessionInvalid("Invalid cafe identity.")

        if (
            request.content_length is not None
            and request.content_length > 1024
        ):
            return _response(
                {"error": "Request too large."}, 413
            )

        if not request.is_json:
            return _response(
                {"error": "JSON request required."}, 415
            )

        raw = request.get_data(cache=True)

        if len(raw) > 1024:
            return _response(
                {"error": "Request too large."}, 413
            )

        body = request.get_json(silent=True)

        if (
            not isinstance(body, dict)
            or set(body) != {"decision"}
            or not isinstance(body["decision"], str)
            or body["decision"] not in DECISIONS
        ):
            return _response(
                {"error": "Invalid booking decision."}, 400
            )

        decision = body["decision"]

        result = change_cafe_reservation_status(
            reservation_id=str(ObjectId(reservation_id)),
            actor_id=partner_id,
            actor_role="cafe_partner",
            decision=decision,
            venue_id=venue_id,
        )

        if (
            not isinstance(result, dict)
            or result.get("reservation_id")
                != str(ObjectId(reservation_id))
            or result.get("venue_id") != venue_id
            or result.get("status") != decision
        ):
            current_app.logger.error(
                "Invalid cafe lifecycle acknowledgement"
            )
            return _response(
                {"error": "Booking update unavailable."}, 503
            )

        return _response({
            "reservation_id": result["reservation_id"],
            "venue_id": result["venue_id"],
            "previous_status": result.get("previous_status"),
            "status": result["status"],
            "message": "Booking updated successfully.",
        }, 200)

    except (CafeSessionInvalid, CafePartnerAccessDenied):
        return _response({"error": "Cafe access denied."}, 401)

    except (
        CafeReservationLifecycleDenied,
        CafeReservationLifecycleServiceDenied,
    ):
        return _response(
            {"error": "Booking action not authorized."}, 403
        )

    except CafeReservationLifecycleConflict:
        return _response(
            {"error": "Booking transition unavailable."}, 409
        )

    except PyMongoError:
        current_app.logger.exception(
            "Cafe booking transaction failed"
        )
        return _response(
            {"error": "Booking update unavailable."}, 503
        )