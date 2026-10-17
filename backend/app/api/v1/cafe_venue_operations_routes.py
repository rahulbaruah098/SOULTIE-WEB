"""Protected API for Xuoroni cafe venue operations.

Facility-only updates are enabled. Opening hours and booking
settings remain locked until booking conflict controls exist.

This blueprint is registered separately by the API initializer.
"""

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.repositories.cafe_venue_operations_repository import (
    CafeVenueOperationsRepositoryError,
)
from app.schemas.cafe_venue_operations import (
    CafeVenueOperationsError,
    validate_cafe_operations_update,
)
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
)
from app.services.cafe_venue_operations_service import (
    CafeVenueOperationsConflict,
    CafeVenueOperationsDenied,
    update_cafe_owner_operations,
)


cafe_venue_operations_bp = Blueprint(
    "cafe_venue_operations",
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
    authorization = request.headers.get("Authorization", "")

    if not isinstance(authorization, str):
        raise CafeSessionInvalid("Invalid cafe session.")

    if not authorization.startswith("Bearer "):
        raise CafeSessionInvalid("Invalid cafe session.")

    token = authorization[7:]

    if (
        not 47 <= len(token) <= 300
        or any(character.isspace() for character in token)
        or "," in token
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return token


@cafe_venue_operations_bp.patch("/venue/operations")
def cafe_owner_update_operations_api():
    """Save validated facilities belonging to the live cafe owner."""
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

        if (
            request.content_length is not None
            and request.content_length > 4096
        ):
            return _response(
                {"error": "Operations request too large."},
                413,
            )

        if not request.is_json:
            return _response(
                {"error": "JSON request required."},
                415,
            )

        raw = request.get_data(cache=True)

        if len(raw) > 4096:
            return _response(
                {"error": "Operations request too large."},
                413,
            )

        changes = validate_cafe_operations_update(
            request.get_json(silent=True)
        )

        result = update_cafe_owner_operations(
            partner_id=partner_id,
            venue_id=venue_id,
            changes=changes,
        )

    except CafeVenueOperationsError:
        return _response(
            {"error": "Invalid venue operations fields."},
            400,
        )

    except CafeVenueOperationsConflict:
        return _response(
            {
                "error": (
                    "Booking-sensitive operations changes "
                    "are not yet available."
                )
            },
            409,
        )

    except (
        CafeSessionInvalid,
        CafePartnerAccessDenied,
        CafeVenueOperationsDenied,
        CafeVenueOperationsRepositoryError,
    ):
        return _response(
            {"error": "Cafe operations access denied."},
            401,
        )

    except PyMongoError:
        current_app.logger.exception(
            "Cafe operations database unavailable"
        )
        return _response(
            {"error": "Cafe operations temporarily unavailable."},
            503,
        )

    if (
        not isinstance(result, dict)
        or result.get("venue_id") != venue_id
        or result.get("status") != "approved"
        or result.get("facilities") != changes.get("facilities")
        or set(changes) != {"facilities"}
    ):
        current_app.logger.error(
            "Invalid cafe operations update acknowledgement"
        )
        return _response(
            {"error": "Cafe operations temporarily unavailable."},
            503,
        )

    return _response(
        {
            "venue_id": venue_id,
            "facilities": list(changes["facilities"]),
            "status": "approved",
            "message": "Cafe facilities updated.",
        },
        200,
    )
