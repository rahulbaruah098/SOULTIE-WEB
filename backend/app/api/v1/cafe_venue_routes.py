"""Authenticated cafe-owner venue profile HTTP API.

A cafe-specific session is mandatory. Dating-user JWTs and
Superadmin credentials do not provide cafe-partner access.
"""

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.schemas.venue import VenueValidationError, validate_venue_profile
from app.repositories.venue_repository import VenueRepositoryError
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)
from app.services.cafe_venue_management_service import (
    CafeVenueManagementDenied,
    get_cafe_owner_venue_profile,
    update_cafe_owner_venue_profile,
)


cafe_venue_bp = Blueprint(
    "cafe_venue",
    __name__,
    url_prefix="/cafe-partners",
)


def _response(body, status):
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _bearer_token():
    raw = request.headers.get("Authorization")
    if (
        not isinstance(raw, str)
        or not 47 <= len(raw) <= 300
        or "," in raw
        or "\r" in raw
        or "\n" in raw
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    parts = raw.split(" ")
    if (
        len(parts) != 2
        or parts[0] != "Bearer"
        or not parts[1]
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return parts[1]


@cafe_venue_bp.get("/venue")
def cafe_owner_venue_profile_api():
    """Return only the authenticated partner's active approved venue."""
    try:
        token = _bearer_token()
        context = authenticate_cafe_session(token=token)

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

        profile = get_cafe_owner_venue_profile(
            partner_id=partner_id,
            venue_id=venue_id,
        )

    except (
        CafeSessionInvalid,
        CafePartnerAccessDenied,
        CafeVenueManagementDenied,
        VenueRepositoryError,
    ):
        return _response(
            {"error": "Cafe venue access denied."},
            401,
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe venue profile database unavailable"
        )
        return _response(
            {"error": "Cafe venue temporarily unavailable."},
            503,
        )

    if not isinstance(profile, dict):
        current_app.logger.error(
            "Cafe venue profile service returned invalid data"
        )
        return _response(
            {"error": "Cafe venue temporarily unavailable."},
            503,
        )

    return _response({"venue": profile}, 200)

@cafe_venue_bp.patch("/venue")
def cafe_owner_update_venue_api():
    """Submit an approved cafe's editable profile for reapproval."""
    try:
        token = _bearer_token()
        context = authenticate_cafe_session(token=token)
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

        if request.content_length is not None and request.content_length > 4096:
            return _response({"error": "Venue update request too large."}, 413)
        if not request.is_json:
            return _response({"error": "JSON request required."}, 415)

        raw = request.get_data(cache=True)
        if len(raw) > 4096:
            return _response({"error": "Venue update request too large."}, 413)

        changes = request.get_json(silent=True)
        clean = validate_venue_profile(changes)
        result = update_cafe_owner_venue_profile(
            partner_id=partner_id,
            venue_id=venue_id,
            changes=clean,
        )

    except VenueValidationError:
        return _response({"error": "Invalid venue profile fields."}, 400)
    except (
        CafeSessionInvalid,
        CafePartnerAccessDenied,
        CafeVenueManagementDenied,
        VenueRepositoryError,
    ):
        return _response({"error": "Cafe venue access denied."}, 401)
    except PyMongoError:
        current_app.logger.exception("Cafe profile update database unavailable")
        return _response({"error": "Venue update temporarily unavailable."}, 503)

    if (
        not isinstance(result, dict)
        or result.get("venue_id") != venue_id
        or result.get("status") != "pending"
        or result.get("requires_reapproval") is not True
    ):
        current_app.logger.error("Invalid venue update acknowledgement")
        return _response({"error": "Venue update temporarily unavailable."}, 503)

    return _response({
        "venue_id": venue_id,
        "status": "pending",
        "requires_reapproval": True,
        "message": "Venue changes submitted for review.",
    }, 200)
