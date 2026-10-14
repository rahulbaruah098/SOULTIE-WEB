"""Authenticated Xuoroni matching API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.match_experience_service import (
    MatchExperienceServiceError,
    get_match_experience,
)
from app.services.matching_service import (
    MatchingServiceError,
    get_my_match,
    list_my_matches,
    submit_profile_action,
    unmatch_my_match,
)


matches_bp = Blueprint(
    "matches_v1",
    __name__,
    url_prefix="/matches",
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


@matches_bp.post("/actions")
@auth_required
def create_profile_action():
    body = request.get_json(
        silent=True
    )

    if not isinstance(
        body,
        dict,
    ):
        return jsonify(
            {
                "error": {
                    "code": (
                        "MATCHING_VALIDATION_ERROR"
                    ),
                    "message": (
                        "A JSON request body "
                        "is required."
                    ),
                }
            }
        ), 400

    try:
        result = submit_profile_action(
            actor_id=(
                g.current_user["_id"]
            ),
            target_id=body.get(
                "target_user_id"
            ),
            action=body.get(
                "action"
            ),
        )

    except MatchingServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@matches_bp.get("")
@auth_required
def get_matches():
    try:
        result = list_my_matches(
            g.current_user["_id"],
            status=request.args.get(
                "status",
                "active",
            ),
            limit=request.args.get(
                "limit",
                50,
            ),
        )

    except MatchingServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@matches_bp.get("/<match_id>")
@auth_required
def get_match(
    match_id,
):
    try:
        match = get_my_match(
            user_id=(
                g.current_user["_id"]
            ),
            match_id=match_id,
        )

    except MatchingServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        {
            "match": match,
        }
    )


@matches_bp.get(
    "/<match_id>/experience"
)
@auth_required
def get_match_experience_route(
    match_id,
):
    try:
        result = get_match_experience(
            user_id=(
                g.current_user["_id"]
            ),
            match_id=match_id,
            icebreaker_limit=(
                request.args.get(
                    "icebreaker_limit",
                    6,
                )
            ),
        )

    except MatchExperienceServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@matches_bp.post(
    "/<match_id>/unmatch"
)
@auth_required
def unmatch_match(
    match_id,
):
    try:
        match = unmatch_my_match(
            user_id=(
                g.current_user["_id"]
            ),
            match_id=match_id,
        )

    except MatchingServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        {
            "match": match,
        }
    )


__all__ = [
    "matches_bp",
]
