
"""Session-authorized Superadmin account moderation."""

from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from pymongo.errors import PyMongoError

from app.security.cafe_superadmin import require_cafe_superadmin
from app.services.account_moderation_service import (
    AccountModerationError,
    moderate_account,
)


account_moderation_bp = Blueprint(
    "account_moderation_v1",
    __name__,
    url_prefix="/account-moderation",
)


def trusted_origin():
    configured = current_app.config.get("FRONTEND_ORIGIN")
    supplied = request.headers.get("Origin")

    if not isinstance(configured, str) or not isinstance(supplied, str):
        return False

    configured = configured.rstrip("/")
    parsed = urlsplit(configured)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False

    return supplied == configured


@account_moderation_bp.post("/users/<user_id>")
@require_cafe_superadmin
def moderate_user(user_id):
    if not trusted_origin():
        return jsonify({
            "error": {"code": "ORIGIN_NOT_ALLOWED"}
        }), 403

    if not request.is_json:
        return jsonify({
            "error": {"code": "JSON_REQUIRED"}
        }), 415

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({
            "error": {"code": "INVALID_BODY"}
        }), 400

    allowed = {"expected_status", "decision", "reason", "report_id"}
    if (
        set(payload) - allowed
        or not {"expected_status", "decision", "reason"}.issubset(payload)
    ):
        return jsonify({
            "error": {"code": "INVALID_FIELDS"}
        }), 400

    try:
        result = moderate_account(
            admin_id=session["admin_id"],
            user_id=user_id,
            expected_status=payload["expected_status"],
            decision=payload["decision"],
            reason=payload["reason"],
            report_id=payload.get("report_id"),
        )
        response = jsonify({"account": result})
        response.headers["Cache-Control"] = "no-store"
        return response
    except AccountModerationError as error:
        return jsonify({
            "error": {"code": error.code, "message": str(error)}
        }), error.status
    except PyMongoError:
        current_app.logger.exception("Account moderation operation failed")
        return jsonify({
            "error": {"code": "MODERATION_UNAVAILABLE"}
        }), 503
