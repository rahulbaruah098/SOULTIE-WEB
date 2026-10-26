"""Authenticated Soultie discovery API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.discovery_service import (
    DiscoveryLifecycleError,
    get_discovery_page,
)
from app.services.discovery_validation import (
    DiscoveryValidationError,
)


discovery_bp = Blueprint(
    "discovery_v1",
    __name__,
    url_prefix="/discovery",
)


def _validation_error(
    error,
):
    return jsonify(
        {
            "error": {
                "code": (
                    "DISCOVERY_VALIDATION_ERROR"
                ),
                "field": error.field,
                "message": error.message,
            }
        }
    ), 400


def _lifecycle_error(
    error,
):
    status = (
        403
        if error.code
        == "DISCOVERY_PROFILE_DISABLED"
        else 409
    )

    return jsonify(
        {
            "error": {
                "code": error.code,
                "message": error.message,
            }
        }
    ), status


@discovery_bp.get("")
@auth_required
def get_discovery():
    try:
        page = get_discovery_page(
            g.current_user["_id"],
            limit=request.args.get(
                "limit"
            ),
            cursor=request.args.get(
                "cursor"
            ),
        )

    except DiscoveryValidationError as exc:
        return _validation_error(
            exc
        )

    except DiscoveryLifecycleError as exc:
        return _lifecycle_error(
            exc
        )

    return jsonify(
        page
    )


__all__ = [
    "discovery_bp",
]
