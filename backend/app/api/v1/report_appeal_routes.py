"""Authenticated appeal submission and protected review endpoints."""

from flask import Blueprint, current_app, g, jsonify, request, session
from pymongo.errors import PyMongoError

from app.security.authentication import auth_required
from app.security.cafe_superadmin import require_cafe_superadmin
from app.services.report_appeal_service import (
    AppealError,
    submit_appeal,
    list_appeals,
    review_appeal,
)

report_appeal_bp = Blueprint(
    "report_appeals_v1",
    __name__,
    url_prefix="/report-appeals",
)


def failure(error):
    return jsonify({
        "error": {"code": error.code, "message": str(error)}
    }), error.status


@report_appeal_bp.post("/reports/<report_id>")
@auth_required
def create_appeal(report_id):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"reason"}:
        return jsonify({
            "error": {"code": "INVALID_BODY"}
        }), 400

    try:
        result = submit_appeal(
            g.current_user["_id"], report_id, payload["reason"]
        )
        return jsonify(result), 201 if result["created"] else 200
    except AppealError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Report appeal submission failed")
        return jsonify({
            "error": {"code": "APPEAL_UNAVAILABLE"}
        }), 503


@report_appeal_bp.get("/admin")
@require_cafe_superadmin
def admin_appeals():
    try:
        result = jsonify(list_appeals(
            status=request.args.get("status", "submitted"),
            limit=request.args.get("limit", 25),
        ))
        result.headers["Cache-Control"] = "no-store"
        return result
    except AppealError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Appeal queue failed")
        return jsonify({
            "error": {"code": "APPEAL_UNAVAILABLE"}
        }), 503


@report_appeal_bp.post("/admin/<appeal_id>/review")
@require_cafe_superadmin
def admin_review(appeal_id):
    # Same-origin protection for authenticated admin sessions.
    origin = request.headers.get("Origin", "").rstrip("/")
    permitted = str(
        current_app.config.get("FRONTEND_ORIGIN") or ""
    ).rstrip("/")

    if not permitted or origin != permitted:
        return jsonify({
            "error": {"code": "ORIGIN_NOT_ALLOWED"}
        }), 403

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({
            "error": {"code": "INVALID_BODY"}
        }), 400

    if set(body) != {"decision", "reason"}:
        return jsonify({
            "error": {"code": "INVALID_FIELDS"}
        }), 400

    try:
        result = review_appeal(
            session["admin_id"],
            appeal_id,
            body["decision"],
            body["reason"],
        )
        return jsonify({"appeal": result})
    except AppealError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Appeal review failed")
        return jsonify({
            "error": {"code": "APPEAL_UNAVAILABLE"}
        }), 503