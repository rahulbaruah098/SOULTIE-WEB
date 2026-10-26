"""Authenticated Soultie onboarding API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.profile_service import (
    ProfileLifecycleError,
    get_onboarding_state,
    update_onboarding_progress,
)


onboarding_bp = Blueprint(
    "onboarding_v1",
    __name__,
    url_prefix="/onboarding",
)


@onboarding_bp.get("/state")
@auth_required
def onboarding_state():
    return jsonify(
        {
            "onboarding": (
                get_onboarding_state(
                    g.current_user["_id"]
                )
            )
        }
    )


@onboarding_bp.patch("/state")
@auth_required
def patch_onboarding_state():
    body = (
        request.get_json(
            silent=True
        )
        or {}
    )

    if not isinstance(
        body,
        dict,
    ):
        return jsonify(
            {
                "error": {
                    "code": (
                        "INVALID_ONBOARDING_REQUEST"
                    ),
                    "message": (
                        "Request body must "
                        "be an object."
                    ),
                }
            }
        ), 400

    unknown = (
        set(body)
        - {
            "current_step",
            "completed_step",
        }
    )

    if unknown:
        return jsonify(
            {
                "error": {
                    "code": (
                        "INVALID_ONBOARDING_REQUEST"
                    ),
                    "message": (
                        "Unsupported onboarding field."
                    ),
                }
            }
        ), 400

    if not body:
        return jsonify(
            {
                "error": {
                    "code": (
                        "INVALID_ONBOARDING_REQUEST"
                    ),
                    "message": (
                        "No onboarding update "
                        "was provided."
                    ),
                }
            }
        ), 400

    try:
        state = (
            update_onboarding_progress(
                g.current_user["_id"],
                current_step=body.get(
                    "current_step"
                ),
                completed_step=body.get(
                    "completed_step"
                ),
            )
        )

    except ProfileLifecycleError as exc:
        return jsonify(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            }
        ), 409

    except ValueError as exc:
        return jsonify(
            {
                "error": {
                    "code": (
                        "INVALID_ONBOARDING_STEP"
                    ),
                    "message": str(
                        exc
                    ),
                }
            }
        ), 400

    return jsonify(
        {
            "onboarding": state,
        }
    )


__all__ = [
    "onboarding_bp",
]


