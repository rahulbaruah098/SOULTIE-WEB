"""Public cafe email verification, without authentication or approval."""

from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.services.cafe_email_verification_service import (
    CafeEmailOtpLimited,
    CafeEmailOtpUnavailable,
    CafeEmailVerificationError,
    request_cafe_email_verification,
    verify_cafe_email_code,
)
from app.services.cafe_partner_auth_service import CafePartnerAuthError


cafe_email_verification_bp = Blueprint(
    "cafe_email_verification",
    __name__,
    url_prefix="/cafe-partners/email",
)

_MAX_BYTES = 2048


def _body(required):
    if request.content_length is not None and request.content_length > _MAX_BYTES:
        return None, (jsonify({"error": "Request too large."}), 413)
    if not request.is_json:
        return None, (jsonify({"error": "JSON required."}), 415)

    raw = request.get_data(cache=True)
    if len(raw) > _MAX_BYTES:
        return None, (jsonify({"error": "Request too large."}), 413)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, (jsonify({"error": "Invalid JSON object."}), 400)

    if set(body) != required:
        return None, (jsonify({"error": "Invalid request fields."}), 400)

    return body, None


def _reply(payload, status):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@cafe_email_verification_bp.post("/request-code")
def request_cafe_email_code_api():
    data, error = _body({"email"})
    if error is not None:
        return error

    try:
        request_cafe_email_verification(email=data["email"])
    except CafeEmailOtpLimited:
        return _reply(
            {"error": "Please wait before requesting another code."}, 429
        )
    except (CafeEmailVerificationError, CafePartnerAuthError):
        # Do not reveal whether a cafe account exists.
        pass
    except (CafeEmailOtpUnavailable, PyMongoError):
        current_app.logger.warning(
            "Cafe verification code request unavailable"
        )
        return _reply(
            {"error": "Verification is temporarily unavailable."}, 503
        )

    return _reply(
        {"message": "If eligible, a verification code will be sent."}, 202
    )


@cafe_email_verification_bp.post("/verify-code")
def verify_cafe_email_code_api():
    data, error = _body({"email", "code"})
    if error is not None:
        return error

    try:
        verified = verify_cafe_email_code(
            email=data["email"],
            code=data["code"],
        )
    except CafeEmailOtpLimited:
        return _reply(
            {"error": "Too many incorrect verification attempts."}, 429
        )
    except (CafeEmailVerificationError, CafePartnerAuthError):
        return _reply(
            {"error": "Invalid or expired verification code."}, 400
        )
    except (CafeEmailOtpUnavailable, PyMongoError):
        current_app.logger.warning(
            "Cafe email verification temporarily unavailable"
        )
        return _reply(
            {"error": "Verification is temporarily unavailable."}, 503
        )

    return _reply(
        {
            "email_verified": verified["email_verified"],
            "status": "pending",
            "message": "Email verified. Await Superadmin approval.",
        },
        200,
    )
