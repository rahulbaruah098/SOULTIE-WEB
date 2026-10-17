"""Isolated cafe partner login API.

Do not register this blueprint until dedicated API security tests pass.
Dating-user and Superadmin identities never use this endpoint.
"""

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
from pymongo.errors import PyMongoError

from app.security.cafe_login_limit import (
    CafeLoginLimitUnavailable,
    CafeLoginRateExceeded,
    check_cafe_login_limit,
)
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services.cafe_partner_auth_service import (
    CafePartnerAuthError,
    authenticate_cafe_partner_password,
)
from app.services.cafe_partner_session_service import (
    CafeSessionInvalid,
    authenticate_cafe_session,
    create_cafe_session,
    revoke_all_cafe_sessions,
    revoke_cafe_session,
)


cafe_auth_bp = Blueprint(
    "cafe_auth",
    __name__,
    url_prefix="/cafe-partners",
)

MAX_LOGIN_BYTES = 2048


def _no_store_response(payload, status):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response, status


@cafe_auth_bp.post("/login")
def cafe_partner_login_api():
    """Authenticate approved cafe owner and issue a cafe-only session."""

    if (
        request.content_length is not None
        and request.content_length > MAX_LOGIN_BYTES
    ):
        return _no_store_response(
            {"error": "Login request too large."}, 413
        )

    if not request.is_json:
        return _no_store_response(
            {"error": "JSON request body required."}, 415
        )

    raw = request.get_data(cache=True)
    if len(raw) > MAX_LOGIN_BYTES:
        return _no_store_response(
            {"error": "Login request too large."}, 413
        )

    body = request.get_json(silent=True)

    # Charge requests even if login credentials are malformed.
    email_for_limit = (
        body.get("email") if isinstance(body, dict) else None
    )

    try:
        check_cafe_login_limit(email=email_for_limit)
    except CafeLoginRateExceeded:
        return _no_store_response({
            "error": "Too many login attempts. Try again later."
        }, 429)
    except CafeLoginLimitUnavailable:
        current_app.logger.warning(
            "Cafe login protection temporarily unavailable"
        )
        return _no_store_response({
            "error": "Login is temporarily unavailable."
        }, 503)

    if (
        not isinstance(body, dict)
        or set(body) != {"email", "password"}
    ):
        return _no_store_response({
            "error": "Email and password are required."
        }, 400)

    if (
        not isinstance(body["email"], str)
        or not isinstance(body["password"], str)
    ):
        return _no_store_response({
            "error": "Invalid email or password."
        }, 401)

    try:
        account = authenticate_cafe_partner_password(
            email=body["email"],
            password=body["password"],
        )
        result = create_cafe_session(
            partner_id=account["partner_id"]
        )
    except (CafePartnerAuthError, CafePartnerAccessDenied):
        return _no_store_response({
            "error": "Invalid credentials or account unavailable."
        }, 401)
    except PyMongoError:
        current_app.logger.exception(
            "Cafe partner login storage unavailable"
        )
        return _no_store_response({
            "error": "Login is temporarily unavailable."
        }, 503)

    return _no_store_response({
        "access_token": result["access_token"],
        "token_type": result["token_type"],
        "expires_in": result["expires_in"],
        "account_type": "cafe_partner",
        "partner_id": result["partner_id"],
        "venue_id": result["venue_id"],
    }, 200)


def _cafe_bearer_token():
    """Require a single reasonably bounded cafe bearer credential."""
    raw = request.headers.get("Authorization")

    if (
        not isinstance(raw, str)
        or not 47 <= len(raw) <= 300
        or "," in raw
        or "\n" in raw
        or "\r" in raw
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    pieces = raw.split(" ")
    if (
        len(pieces) != 2
        or pieces[0] != "Bearer"
        or not pieces[1]
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return pieces[1]


@cafe_auth_bp.get("/me")
def cafe_partner_me_api():
    """Return only the current approved cafe identity."""
    try:
        token = _cafe_bearer_token()
        context = authenticate_cafe_session(token=token)
    except CafeSessionInvalid:
        return _no_store_response(
            {"error": "Invalid or expired cafe session."}, 401
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe profile session storage unavailable"
        )
        return _no_store_response(
            {"error": "Cafe session temporarily unavailable."}, 503
        )

    return _no_store_response({
        "account_type": "cafe_partner",
        "partner_id": context["partner_id"],
        "venue_id": context["venue_id"],
    }, 200)


@cafe_auth_bp.post("/logout")
def cafe_partner_logout_api():
    """Revoke only the presented cafe session."""
    try:
        token = _cafe_bearer_token()
        revoked = revoke_cafe_session(token=token)
    except CafeSessionInvalid:
        return _no_store_response(
            {"error": "Invalid cafe session."}, 401
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe logout storage unavailable"
        )
        return _no_store_response(
            {"error": "Logout temporarily unavailable."}, 503
        )

    if not revoked:
        return _no_store_response(
            {"error": "Invalid cafe session."}, 401
        )

    return _no_store_response({
        "message": "Cafe session logged out."
    }, 200)

@cafe_auth_bp.post("/logout-all")
def cafe_partner_logout_all_api():
    """Revoke sessions belonging only to the authenticated cafe."""
    try:
        token = _cafe_bearer_token()
        context = authenticate_cafe_session(token=token)
        partner_id = context["partner_id"]
        if (
            not isinstance(partner_id, str)
            or not ObjectId.is_valid(partner_id)
        ):
            raise CafeSessionInvalid("Invalid cafe session.")
        count = revoke_all_cafe_sessions(
            partner_id=partner_id
        )
    except (CafeSessionInvalid, KeyError, TypeError):
        return _no_store_response({
            "error": "Invalid or expired cafe session."
        }, 401)
    except PyMongoError:
        current_app.logger.exception(
            "Cafe logout-all session storage unavailable"
        )
        return _no_store_response({
            "error": "Logout temporarily unavailable."
        }, 503)

    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
    ):
        current_app.logger.error(
            "Cafe logout-all returned invalid revocation count"
        )
        return _no_store_response({
            "error": "Logout temporarily unavailable."
        }, 503)

    return _no_store_response({
        "message": "All cafe sessions logged out.",
        "revoked_sessions": count,
    }, 200)
