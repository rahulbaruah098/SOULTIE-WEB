"""Private Superadmin report moderation endpoints."""

from flask import Blueprint, current_app, jsonify, request, session
from pymongo.errors import PyMongoError

from app.security.cafe_superadmin import require_cafe_superadmin
from app.services.moderation_case_service import (
    CaseError,
    decide_case,
    list_cases,
)


report_moderation_bp = Blueprint(
    "report_moderation_v1",
    __name__,
    url_prefix="/report-moderation",
)


def error_response(error):
    return jsonify({
        "error": {"code": error.code, "message": str(error)}
    }), error.status


@report_moderation_bp.get("/cases")
@require_cafe_superadmin
def get_cases():
    try:
        response = jsonify(list_cases(
            status=request.args.get("status", "submitted"),
            limit=request.args.get("limit", 25),
            after=request.args.get("after_id"),
        ))
        response.headers["Cache-Control"] = "no-store"
        return response
    except CaseError as error:
        return error_response(error)
    except PyMongoError:
        current_app.logger.exception("Report moderation query failed")
        return jsonify({"error": {"code": "MODERATION_UNAVAILABLE"}}), 503


@report_moderation_bp.post("/cases/<report_id>/decide")
@require_cafe_superadmin
def post_decision(report_id):
    origin = request.headers.get("Origin", "").rstrip("/")
    expected_origin = str(
        current_app.config.get("FRONTEND_ORIGIN") or ""
    ).rstrip("/")

    if not expected_origin or origin != expected_origin:
        return jsonify({"error": {"code": "ORIGIN_NOT_ALLOWED"}}), 403

    if not request.is_json:
        return jsonify({"error": {"code": "JSON_REQUIRED"}}), 415

    try:
        result = decide_case(
            actor_id=session["admin_id"],
            report_id=report_id,
            payload=request.get_json(silent=True),
        )
        response = jsonify({"case": result})
        response.headers["Cache-Control"] = "no-store"
        return response
    except CaseError as error:
        return error_response(error)
    except PyMongoError:
        current_app.logger.exception("Report moderation decision failed")
        return jsonify({"error": {"code": "MODERATION_UNAVAILABLE"}}), 503


@report_moderation_bp.post("/cases/<report_id>/notes")
@require_cafe_superadmin
def post_case_note(report_id):
    """Superadmin-only internal moderation note."""
    from app.services.moderation_case_service import add_case_note

    origin = request.headers.get("Origin", "").rstrip("/")
    expected_origin = str(
        current_app.config.get("FRONTEND_ORIGIN") or ""
    ).rstrip("/")

    if not expected_origin or origin != expected_origin:
        return jsonify({
            "error": {"code": "ORIGIN_NOT_ALLOWED"}
        }), 403

    if not request.is_json:
        return jsonify({
            "error": {"code": "JSON_REQUIRED"}
        }), 415

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {
        "note_id", "content"
    }:
        return jsonify({
            "error": {"code": "INVALID_FIELDS"}
        }), 400

    try:
        result = add_case_note(
            session["admin_id"],
            report_id,
            body["note_id"],
            body["content"],
        )
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response, 201 if result["created"] else 200
    except ModerationCaseError as error:
        return _failure(error)
    except PyMongoError:
        current_app.logger.exception(
            "Failed to save private moderation note"
        )
        return jsonify({
            "error": {"code": "MODERATION_UNAVAILABLE"}
        }), 503


@report_moderation_bp.get("/cases/<report_id>/notes")
@require_cafe_superadmin
def get_case_notes(report_id):
    """Private notes; never exposed through public report APIs."""
    from app.services.moderation_case_service import list_case_notes

    try:
        response = jsonify(list_case_notes(
            session["admin_id"],
            report_id,
            request.args.get("limit", 50),
        ))
        response.headers["Cache-Control"] = "no-store"
        return response
    except ModerationCaseError as error:
        return _failure(error)
    except PyMongoError:
        current_app.logger.exception("Moderation note retrieval failed")
        return jsonify({
            "error": {"code": "MODERATION_UNAVAILABLE"}
        }), 503
