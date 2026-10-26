"""JWT-authenticated user blocking endpoints."""

from flask import Blueprint, g, jsonify, request, current_app
from pymongo.errors import PyMongoError
from app.security.authentication import auth_required
from app.services.user_block_service import (
    BlockError, block_user, unblock_user, list_blocked_users,
)

user_blocks_bp = Blueprint(
    "user_blocks_v1", __name__, url_prefix="/blocks"
)


def failure(error):
    return jsonify({
        "error": {"code": error.code, "message": str(error)}
    }), error.status


@user_blocks_bp.post("/<target_id>")
@auth_required
def block_target(target_id):
    try:
        result = block_user(g.current_user["_id"], target_id)
        return jsonify(result)
    except BlockError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Block operation failed")
        return jsonify({"error": {"code": "BLOCK_UNAVAILABLE"}}), 503


@user_blocks_bp.delete("/<target_id>")
@auth_required
def unblock_target(target_id):
    try:
        return jsonify(unblock_user(g.current_user["_id"], target_id))
    except BlockError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Unblock operation failed")
        return jsonify({"error": {"code": "BLOCK_UNAVAILABLE"}}), 503


@user_blocks_bp.get("")
@auth_required
def blocked_users():
    try:
        return jsonify(list_blocked_users(
            g.current_user["_id"],
            request.args.get("limit", 50),
        ))
    except BlockError as error:
        return failure(error)
    except PyMongoError:
        current_app.logger.exception("Block listing failed")
        return jsonify({"error": {"code": "BLOCK_UNAVAILABLE"}}), 503
