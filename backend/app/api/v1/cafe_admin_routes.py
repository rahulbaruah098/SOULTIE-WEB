"""Privileged cafe moderation endpoints.

Blueprint registration is deliberately separate. Every moderation
request requires an active Superadmin session and matching Origin.
"""

from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from pymongo.errors import PyMongoError

from app.security.cafe_superadmin import require_cafe_superadmin
from app.services.cafe_moderation_service import (
    CafeModerationConflict,
    CafeModerationError,
    moderate_cafe_venue,
)


cafe_admin_bp = Blueprint(
    "cafe_admin",
    __name__,
    url_prefix="/cafe-admin",
)


def _trusted_origin():
    """Allow browser mutations only from configured frontend origin."""
    configured = current_app.config.get("FRONTEND_ORIGIN")
    supplied = request.headers.get("Origin")

    if not isinstance(configured, str) or not isinstance(supplied, str):
        return False

    configured = configured.rstrip("/")
    parsed = urlsplit(configured)

    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False

    return supplied == configured


@cafe_admin_bp.post("/venues/<venue_id>/moderate")
@require_cafe_superadmin
def moderate_venue_api(venue_id):
    """Moderate a cafe belonging to a verified partner account."""
    if not _trusted_origin():
        return jsonify({
            "error": "Request origin is not allowed."
        }), 403

    if not request.is_json:
        return jsonify({
            "error": "JSON request body required."
        }), 415

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({
            "error": "Invalid JSON request body."
        }), 400

    allowed = {"expected_status", "decision", "reason"}
    if set(payload) - allowed:
        return jsonify({
            "error": "Unexpected request fields."
        }), 400

    expected_status = payload.get("expected_status")
    decision = payload.get("decision")
    reason = payload.get("reason")

    if (
        not isinstance(expected_status, str)
        or not isinstance(decision, str)
    ):
        return jsonify({
            "error": "Valid status and decision are required."
        }), 400

    # The actor is always taken from the authenticated session,
    # never from an ID provided in a request body or URL.
    try:
        result = moderate_cafe_venue(
            superadmin_id=session["admin_id"],
            venue_id=venue_id,
            expected_status=expected_status,
            decision=decision,
            reason=reason,
        )
    except CafeModerationConflict as error:
        return jsonify({"error": str(error)}), 409
    except CafeModerationError as error:
        return jsonify({"error": str(error)}), 400
    except PyMongoError:
        current_app.logger.exception(
            "Cafe moderation database transaction failed"
        )
        return jsonify({
            "error": "Cafe moderation is temporarily unavailable."
        }), 503

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response, 200



@cafe_admin_bp.get("/venues")
@require_cafe_superadmin
def list_cafe_venues_api():
    """Show a limited, paginated Superadmin review queue."""
    from bson import ObjectId
    from app.extensions import mongo

    allowed_statuses = {
        "pending", "approved", "rejected", "suspended"
    }
    status = request.args.get("status", "pending")
    raw_limit = request.args.get("limit", "25")
    after_id = request.args.get("after_id")

    if status not in allowed_statuses:
        return jsonify({
            "error": "Invalid cafe application status."
        }), 400

    if (
        not raw_limit.isascii()
        or not raw_limit.isdecimal()
        or not 1 <= len(raw_limit) <= 2
    ):
        return jsonify({"error": "Invalid page limit."}), 400

    limit = int(raw_limit)
    if not 1 <= limit <= 50:
        return jsonify({"error": "Invalid page limit."}), 400

    if after_id is not None and not ObjectId.is_valid(after_id):
        return jsonify({"error": "Invalid pagination cursor."}), 400

    query = {"status": status}
    if after_id is not None:
        query["_id"] = {"$lt": ObjectId(after_id)}

    # Explicit allowlist: do not expose account credentials or
    # arbitrary fields from submitted venue documents.
    projection = {
        "_id": 1,
        "owner_id": 1,
        "name": 1,
        "city": 1,
        "address": 1,
        "status": 1,
        "created_at": 1,
        "updated_at": 1,
        "reviewed_at": 1,
        "review_reason": 1,
    }

    try:
        records = list(
            mongo.db.venues.find(query, projection)
            .sort("_id", -1)
            .limit(limit + 1)
        )
    except PyMongoError:
        current_app.logger.exception(
            "Cafe moderation queue could not be loaded"
        )
        return jsonify({
            "error": "Cafe applications are temporarily unavailable."
        }), 503

    has_more = len(records) > limit
    selected = records[:limit]

    def safe_date(value):
        return value.isoformat() if hasattr(value, "isoformat") else None

    items = []
    for venue in selected:
        items.append({
            "id": str(venue["_id"]),
            "owner_id": (
                str(venue["owner_id"])
                if venue.get("owner_id") is not None else None
            ),
            "name": venue.get("name"),
            "city": venue.get("city"),
            "address": venue.get("address"),
            "status": venue.get("status"),
            "created_at": safe_date(venue.get("created_at")),
            "updated_at": safe_date(venue.get("updated_at")),
            "reviewed_at": safe_date(venue.get("reviewed_at")),
            "review_reason": venue.get("review_reason"),
        })

    response = jsonify({
        "items": items,
        "has_more": has_more,
        "next_cursor": (
            str(selected[-1]["_id"])
            if has_more and selected else None
        ),
    })
    response.headers["Cache-Control"] = "no-store"
    return response, 200
