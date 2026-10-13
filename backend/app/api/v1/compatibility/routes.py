"""Authenticated Xuoroni compatibility API."""

from flask import (
    Blueprint,
    g,
    jsonify,
)

from app.security.authentication import (
    auth_required,
)
from app.services.compatibility_service import (
    CompatibilityServiceError,
    get_profile_compatibility,
)


compatibility_bp = Blueprint(
    "compatibility_v1",
    __name__,
    url_prefix="/compatibility",
)


def _service_error(
    error,
):
    return jsonify(
        {
            "error": {
                "code": error.code,
                "message": error.message,
            }
        }
    ), error.status_code


@compatibility_bp.get(
    "/<target_user_id>"
)
@auth_required
def get_compatibility(
    target_user_id,
):
    try:
        result = get_profile_compatibility(
            requester_id=(
                g.current_user["_id"]
            ),
            target_user_id=(
                target_user_id
            ),
        )

    except CompatibilityServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


__all__ = [
    "compatibility_bp",
]