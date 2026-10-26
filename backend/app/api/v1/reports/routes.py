"""Authenticated user reporting routes."""

from flask import Blueprint, g, jsonify, request

from app.security.authentication import auth_required
from app.services.report_service import (
    ReportError,
    create_report,
    list_my_reports,
)

reports_bp = Blueprint("reports_v1", __name__, url_prefix="/reports")


@reports_bp.post("")
@auth_required
def submit_report():
    try:
        result = create_report(
            g.current_user["_id"],
            request.get_json(silent=True),
        )
        return jsonify(result), 201 if result["created"] else 200
    except ReportError as exc:
        return jsonify({
            "error": {"code": exc.code, "message": str(exc)}
        }), exc.status


@reports_bp.get("")
@auth_required
def my_reports():
    try:
        return jsonify(list_my_reports(
            g.current_user["_id"],
            request.args.get("limit", 20),
        ))
    except ReportError as exc:
        return jsonify({
            "error": {"code": exc.code, "message": str(exc)}
        }), exc.status
