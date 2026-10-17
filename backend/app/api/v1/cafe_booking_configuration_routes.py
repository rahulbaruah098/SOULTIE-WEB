"""Authenticated cafe owner API for complete booking configuration.

Identity is derived ONLY from a verified cafe partner session.
Configuration updates are authorized and transactional.

This blueprint must be registered by the existing API initializer
in a separate integration checkpoint. Never add it to dating-user
or admin authentication namespaces.
"""

from bson import ObjectId
from flask import Blueprint, jsonify, request
from pymongo.errors import PyMongoError

from app.schemas.cafe_booking_configuration import (
    CafeBookingConfigurationError,
)
from app.schemas.cafe_venue_operations import (
    CafeVenueOperationsError,
)
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)
from app.services.cafe_booking_configuration_service import (
    CafeBookingConfigurationAccessDenied,
    update_authorized_cafe_booking_configuration,
)
from app.repositories.cafe_booking_configuration_repository import (
    CafeBookingConfigurationDenied,
    CafeBookingConfigurationConflict,
)


cafe_booking_configuration_bp = Blueprint(
    "cafe_booking_configuration",
    __name__,
    url_prefix="/cafe-partners",
)

_ALLOWED_BODY = frozenset({
    "configuration",
    "weekly_hours",
    "booking_settings",
})

_MAX_BODY_BYTES = 8192


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


def _session_identity():
    context = authenticate_cafe_session(token=_bearer_token())

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

    return str(ObjectId(partner_id)), str(ObjectId(venue_id))


@cafe_booking_configuration_bp.patch("/venue/booking-configuration")
def update_cafe_booking_configuration_api():
    """Save complete booking settings for the authenticated owner."""
    try:
        partner_id, venue_id = _session_identity()

        if (
            request.content_length is not None
            and request.content_length > _MAX_BODY_BYTES
        ):
            return _response({"error": "Request too large."}, 413)

        if not request.is_json:
            return _response({"error": "JSON request required."}, 415)

        raw = request.get_data(cache=True)

        if len(raw) > _MAX_BODY_BYTES:
            return _response({"error": "Request too large."}, 413)

        body = request.get_json(silent=True)

        if not isinstance(body, dict) or set(body) != _ALLOWED_BODY:
            return _response(
                {"error": "Invalid booking configuration request."},
                400,
            )

        result = update_authorized_cafe_booking_configuration(
            partner_id=partner_id,
            venue_id=venue_id,
            configuration=body["configuration"],
            weekly_hours=body["weekly_hours"],
            booking_settings=body["booking_settings"],
        )

        if (
            not isinstance(result, dict)
            or result.get("configuration_saved") is not True
            or result.get("venue_id") != venue_id
        ):
            return _response(
                {"error": "Configuration update could not be verified."},
                503,
            )

        return _response({
            "venue_id": venue_id,
            "seat_capacity": result["seat_capacity"],
            "timezone": result["timezone"],
            "weekly_hours": result["weekly_hours"],
            "booking_settings": result["booking_settings"],
            "configuration_saved": True,
        }, 200)

    except (
        CafeBookingConfigurationError,
        CafeVenueOperationsError,
    ):
        return _response(
            {"error": "Invalid booking configuration fields."},
            400,
        )

    except CafeBookingConfigurationConflict:
        return _response(
            {
                "error": (
                    "Existing reservations prevent changes "
                    "to booking configuration."
                )
            },
            409,
        )

    except (
        CafeSessionInvalid,
        CafePartnerAccessDenied,
        CafeBookingConfigurationAccessDenied,
        CafeBookingConfigurationDenied,
    ):
        return _response({"error": "Cafe access denied."}, 403)

    except PyMongoError:
        return _response(
            {"error": "Booking configuration temporarily unavailable."},
            503,
        )
