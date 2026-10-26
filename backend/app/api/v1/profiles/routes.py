"""Authenticated Soultie profile API."""

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
    get_my_discovery_preferences,
    get_my_profile,
    serialize_discovery_preferences_for_owner,
    serialize_profile_for_owner,
    update_my_discovery_preferences,
    update_my_profile,
)
from app.services.profile_validation import (
    ProfileValidationError,
)


profiles_bp = Blueprint(
    "profiles_v1",
    __name__,
    url_prefix="/profile",
)


def _validation_error(
    error,
):
    return jsonify(
        {
            "error": {
                "code": (
                    "PROFILE_VALIDATION_ERROR"
                ),
                "field": error.field,
                "message": error.message,
            }
        }
    ), 400


def _lifecycle_error(
    error,
):
    return jsonify(
        {
            "error": {
                "code": error.code,
                "message": error.message,
            }
        }
    ), 409


@profiles_bp.get("/me")
@auth_required
def get_profile_me():
    profile = get_my_profile(
        g.current_user["_id"]
    )

    return jsonify(
        {
            "profile": (
                serialize_profile_for_owner(
                    profile
                )
            )
        }
    )


@profiles_bp.patch("/me")
@auth_required
def patch_profile_me():
    body = request.get_json(
        silent=True
    )

    try:
        profile = update_my_profile(
            g.current_user["_id"],
            body,
        )

    except ProfileValidationError as exc:
        return _validation_error(
            exc
        )

    except ProfileLifecycleError as exc:
        return _lifecycle_error(
            exc
        )

    return jsonify(
        {
            "profile": (
                serialize_profile_for_owner(
                    profile
                )
            )
        }
    )


@profiles_bp.get(
    "/me/discovery-preferences"
)
@auth_required
def get_discovery_preferences_me():
    preferences = (
        get_my_discovery_preferences(
            g.current_user["_id"]
        )
    )

    return jsonify(
        {
            "preferences": (
                serialize_discovery_preferences_for_owner(
                    preferences
                )
            )
        }
    )


@profiles_bp.patch(
    "/me/discovery-preferences"
)
@auth_required
def patch_discovery_preferences_me():
    body = request.get_json(
        silent=True
    )

    try:
        preferences = (
            update_my_discovery_preferences(
                g.current_user["_id"],
                body,
            )
        )

    except ProfileValidationError as exc:
        return _validation_error(
            exc
        )

    except ProfileLifecycleError as exc:
        return _lifecycle_error(
            exc
        )

    return jsonify(
        {
            "preferences": (
                serialize_discovery_preferences_for_owner(
                    preferences
                )
            )
        }
    )


__all__ = [
    "profiles_bp",
]



