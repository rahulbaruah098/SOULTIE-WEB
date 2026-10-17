"""Authorization boundary for Xuoroni Superadmin caf? APIs.

Legacy admin authentication is preserved. New caf? moderation routes
must use this stricter decorator, not legacy admin_required alone.

An authenticated session is insufficient: the admin account must
currently exist in MongoDB and explicitly have role super_admin.
"""

from functools import wraps

from bson import ObjectId
from flask import jsonify, session

from app.extensions import mongo


SUPERADMIN_ROLE = "super_admin"


def require_cafe_superadmin(fn):
    """Require an active, explicitly privileged website admin."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        raw_id = session.get("admin_id")

        if (
            not isinstance(raw_id, str)
            or not ObjectId.is_valid(raw_id)
        ):
            return jsonify({
                "error": "Superadmin authentication required."
            }), 401

        admin = mongo.db.admins.find_one({
            "_id": ObjectId(raw_id),
        })

        if admin is None:
            return jsonify({
                "error": "Superadmin authentication required."
            }), 401

        if (
            admin.get("role") != SUPERADMIN_ROLE
            or admin.get("is_active") is False
            or admin.get("status", "active") != "active"
        ):
            return jsonify({
                "error": "Superadmin access required."
            }), 403

        return fn(*args, **kwargs)

    return wrapped
