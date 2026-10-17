"""Public Xuoroni cafe partner registration endpoint.

This blueprint is registered separately after endpoint security tests.
Registration does not create a session or grant venue approval.
"""

from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.schemas.venue import (
    REGISTRATION_FIELDS,
    VenueValidationError,
)
from app.security.cafe_registration_limit import (
    CafeRegistrationLimitError,
    CafeRegistrationRateExceeded,
    check_cafe_registration_limit,
)
from app.services.cafe_partner_auth_service import (
    CafePartnerAlreadyRegistered,
    CafePartnerAuthError,
)
from app.services.cafe_registration_service import (
    CafeRegistrationError,
    register_cafe_with_venue,
)


cafe_registration_bp = Blueprint(
    "cafe_registration",
    __name__,
    url_prefix="/cafe-partners",
)

MAX_REGISTRATION_BYTES = 8192


@cafe_registration_bp.post("/register")
def register_cafe_partner_api():
    """Create one pending cafe partner and its owned venue."""
    # Never accept arbitrarily large JSON before rate limiting.
    if (
        request.content_length is not None
        and request.content_length > MAX_REGISTRATION_BYTES
    ):
        return jsonify({"error": "Registration request too large."}), 413

    try:
        check_cafe_registration_limit()
    except CafeRegistrationRateExceeded:
        response = jsonify({
            "error": "Too many registration attempts. Try again later."
        })
        response.headers["Cache-Control"] = "no-store"
        return response, 429
    except CafeRegistrationLimitError:
        current_app.logger.warning(
            "Cafe registration limiter unavailable"
        )
        return jsonify({
            "error": "Registration is temporarily unavailable."
        }), 503

    if not request.is_json:
        return jsonify({"error": "JSON request body required."}), 415

    # Enforce the actual body length even for chunked requests.
    raw = request.get_data(cache=True)
    if len(raw) > MAX_REGISTRATION_BYTES:
        return jsonify({"error": "Registration request too large."}), 413

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid registration JSON."}), 400

    allowed_fields = set(REGISTRATION_FIELDS) | {"password"}
    if set(body) - allowed_fields:
        return jsonify({
            "error": "Unsupported registration fields."
        }), 400

    if "password" not in body:
        return jsonify({"error": "Password is required."}), 400

    registration = {
        key: value
        for key, value in body.items()
        if key != "password"
    }

    try:
        result = register_cafe_with_venue(
            registration=registration,
            password=body["password"],
        )
    except CafePartnerAlreadyRegistered:
        return jsonify({
            "error": "An account with this email already exists."
        }), 409
    except (
        CafePartnerAuthError,
        CafeRegistrationError,
        VenueValidationError,
    ) as error:
        return jsonify({"error": str(error)}), 400
    except PyMongoError:
        current_app.logger.exception(
            "Transactional cafe registration failed"
        )
        return jsonify({
            "error": "Registration is temporarily unavailable."
        }), 503

    # Whitelist output fields. Never return a password hash.
    response = jsonify({
        "partner_id": result["partner_id"],
        "venue_id": result["venue_id"],
        "status": "pending",
        "email_verified": False,
        "message": (
            "Registration received. Email verification and "
            "Superadmin approval are required."
        ),
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 202
