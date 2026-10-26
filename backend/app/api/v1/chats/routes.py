"""Authenticated Soultie chat REST API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.chat_service import (
    ChatServiceError,
    ensure_chat_for_match,
    get_my_conversation,
    list_my_conversations,
    list_my_messages,
    mark_chat_delivered,
    mark_chat_read,
    send_chat_message,
)


chats_bp = Blueprint(
    "chats_v1",
    __name__,
    url_prefix="/chats",
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


def _validation_error(
    message,
):
    return jsonify(
        {
            "error": {
                "code": (
                    "CHAT_VALIDATION_ERROR"
                ),
                "message": message,
            }
        }
    ), 400


def _required_json_body():
    body = request.get_json(
        silent=True
    )

    if not isinstance(
        body,
        dict,
    ):
        return (
            None,
            _validation_error(
                (
                    "A JSON request body "
                    "is required."
                )
            ),
        )

    return (
        body,
        None,
    )


def _optional_json_body():
    raw_body = request.get_data(
        cache=True
    )

    if not raw_body:
        return (
            {},
            None,
        )

    body = request.get_json(
        silent=True
    )

    if not isinstance(
        body,
        dict,
    ):
        return (
            None,
            _validation_error(
                (
                    "Request body must "
                    "be a JSON object."
                )
            ),
        )

    return (
        body,
        None,
    )


@chats_bp.post(
    "/matches/<match_id>"
)
@auth_required
def create_or_get_chat(
    match_id,
):
    try:
        result = ensure_chat_for_match(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            match_id=match_id,
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    status_code = (
        201
        if result.get(
            "created"
        )
        else 200
    )

    return jsonify(
        result
    ), status_code


@chats_bp.get("")
@auth_required
def list_chats():
    try:
        result = list_my_conversations(
            g.current_user[
                "_id"
            ],
            status=request.args.get(
                "status",
                "active",
            ),
            limit=request.args.get(
                "limit",
                "50",
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.get(
    "/<conversation_id>"
)
@auth_required
def get_chat(
    conversation_id,
):
    try:
        result = get_my_conversation(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.get(
    "/<conversation_id>/messages"
)
@auth_required
def get_chat_messages(
    conversation_id,
):
    try:
        result = list_my_messages(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            before_sequence=(
                request.args.get(
                    "before_sequence"
                )
            ),
            limit=request.args.get(
                "limit"
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.post(
    "/<conversation_id>/messages"
)
@auth_required
def create_chat_message(
    conversation_id,
):
    body, error_response = (
        _required_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = send_chat_message(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            client_message_id=(
                body.get(
                    "client_message_id"
                )
            ),
            body=body.get(
                "body"
            ),
            message_type=body.get(
                "message_type",
                "text",
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    status_code = (
        201
        if result.get(
            "created"
        )
        else 200
    )

    return jsonify(
        result
    ), status_code


@chats_bp.post(
    "/<conversation_id>/delivered"
)
@auth_required
def mark_chat_messages_delivered(
    conversation_id,
):
    body, error_response = (
        _optional_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = mark_chat_delivered(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            through_sequence=(
                body.get(
                    "through_sequence"
                )
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.post(
    "/<conversation_id>/read"
)
@auth_required
def mark_chat_messages_read(
    conversation_id,
):
    body, error_response = (
        _optional_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = mark_chat_read(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            through_sequence=(
                body.get(
                    "through_sequence"
                )
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


__all__ = [
    "chats_bp",
]



@chats_bp.post("/<conversation_id>/cafe-suggestions")
@auth_required
def suggest_chat_cafes(conversation_id):
    """Read-only cafe suggestions for authorized chat participants."""
    from datetime import date
    from bson import ObjectId
    from pymongo.errors import PyMongoError
    from app.services.cafe_chat_authorized_recommendation_service import (
        get_authorized_chat_cafe_suggestions,
    )
    from app.services.cafe_chat_booking_action_service import (
        CafeChatAvailabilityUnavailable,
    )
    from app.services.cafe_chat_venue_recommendation_service import (
        CafeChatRecommendationUnavailable,
    )

    def error(code, message, status):
        response = jsonify({
            "error": {"code": code, "message": message}
        })
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    principal = getattr(g, "current_user", None)
    uid = principal.get("_id") if isinstance(principal, dict) else None
    if not isinstance(uid, ObjectId):
        return error("AUTH_REQUIRED", "Authentication required.", 401)

    if not ObjectId.is_valid(conversation_id):
        return error("CHAT_VALIDATION_ERROR", "Invalid conversation.", 400)

    if request.content_length is not None and request.content_length > 4096:
        return error("REQUEST_TOO_LARGE", "Request too large.", 413)

    if not request.is_json:
        return error("JSON_REQUIRED", "JSON request required.", 415)

    if len(request.get_data(cache=True)) > 4096:
        return error("REQUEST_TOO_LARGE", "Request too large.", 413)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error("CHAT_VALIDATION_ERROR", "Invalid JSON object.", 400)

    if set(body) - {"message", "booking_date", "party_size", "limit"}:
        return error("CHAT_VALIDATION_ERROR", "Unexpected fields.", 400)

    message = body.get("message")
    limit = body.get("limit", 5)
    if not isinstance(message, str) or not 1 <= len(message) <= 1500:
        return error("CHAT_VALIDATION_ERROR", "Invalid message.", 400)
    if type(limit) is not int or not 1 <= limit <= 5:
        return error("CHAT_VALIDATION_ERROR", "Invalid limit.", 400)

    raw_date = body.get("booking_date")
    party_size = body.get("party_size")
    if (raw_date is None) != (party_size is None):
        return error("CHAT_VALIDATION_ERROR", "Date and party size required together.", 400)

    parsed_date = None
    if raw_date is not None:
        if not isinstance(raw_date, str) or len(raw_date) != 10:
            return error("CHAT_VALIDATION_ERROR", "Invalid date.", 400)
        try:
            parsed_date = date.fromisoformat(raw_date)
            if parsed_date.isoformat() != raw_date:
                raise ValueError("Noncanonical date")
        except ValueError:
            return error("CHAT_VALIDATION_ERROR", "Invalid date.", 400)
        if type(party_size) is not int or not 1 <= party_size <= 100:
            return error("CHAT_VALIDATION_ERROR", "Invalid party size.", 400)

    try:
        result = get_authorized_chat_cafe_suggestions(
            user_id=uid,
            conversation_id=conversation_id,
            message=message,
            booking_date=parsed_date,
            party_size=party_size,
            limit=limit,
        )
    except ChatServiceError as exc:
        response, status = _service_error(exc)
        response.headers["Cache-Control"] = "no-store"
        return response, status
    except ValueError:
        return error("CHAT_VALIDATION_ERROR", "Invalid request.", 400)
    except (
        CafeChatAvailabilityUnavailable,
        CafeChatRecommendationUnavailable,
        PyMongoError,
    ):
        return error(
            "CAFE_SUGGESTIONS_UNAVAILABLE",
            "Suggestions temporarily unavailable.",
            503,
        )

    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

# Phase 2M: register authenticated Safe Meet endpoints.
from app.api.v1.chats.safe_meet_routes import register_safe_meet_routes
register_safe_meet_routes(chats_bp, auth_required)

# SOULTIE_MATCHING_M2_CULTURE_PREFERENCES
_M2_FIELDS = (
    "languages",
    "festivals",
    "food_interests",
    "music_interests",
    "lifestyle",
    "relationship_goals",
)


def _m2_validate_preferences(value):
    if not isinstance(value, dict):
        raise ValueError("culture_preferences must be a JSON object.")

    if set(value) - set(_M2_FIELDS):
        raise ValueError("Unsupported culture preference field.")

    cleaned = {}
    for field in _M2_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list) or len(items) > 20:
            raise ValueError("Each preference must be a list of at most 20 items.")

        output = []
        seen = set()
        for item in items:
            if not isinstance(item, str):
                raise ValueError("Preference items must be text.")

            text = " ".join(item.strip().split())
            if (
                not text
                or len(text) > 60
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in item)
            ):
                raise ValueError("Invalid culture preference item.")

            normalized = text.casefold()
            if normalized not in seen:
                output.append(text)
                seen.add(normalized)

        cleaned[field] = output

    return cleaned


def _m2_profile_view(profile):
    raw = profile.get("culture_preferences")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "culture_preferences": {
            field: raw.get(field, []) if isinstance(raw.get(field, []), list) else []
            for field in _M2_FIELDS
        },
        "culture_compatibility_enabled": (
            profile.get("culture_compatibility_enabled") is True
        ),
    }


def _m2_current_profile():
    from bson import ObjectId
    from app.extensions import mongo

    actor = g.current_user["_id"]
    actor = actor if isinstance(actor, ObjectId) else ObjectId(str(actor))

    return mongo.db.profiles.find_one({
        "user_id": actor,
        "onboarding_status": "completed",
        "profile_status": "active",
    })


@chats_bp.get("/culture-preferences")
@auth_required
def get_my_culture_preferences():
    profile = _m2_current_profile()
    if profile is None:
        return jsonify({
            "error": {
                "code": "PROFILE_UNAVAILABLE",
                "message": "An active completed profile is required.",
            }
        }), 404

    return jsonify(_m2_profile_view(profile)), 200


@chats_bp.put("/culture-preferences")
@auth_required
def update_my_culture_preferences():
    from datetime import datetime, timezone
    from bson import ObjectId
    from app.extensions import mongo

    if not request.is_json:
        return _validation_error("A JSON request body is required.")

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return _validation_error("A non-empty JSON object is required.")

    permitted = {
        "culture_preferences",
        "culture_compatibility_enabled",
    }

    if set(data) - permitted:
        return _validation_error("Unsupported preference update field.")

    updates = {}
    if "culture_preferences" in data:
        try:
            updates["culture_preferences"] = _m2_validate_preferences(
                data["culture_preferences"]
            )
        except ValueError as exc:
            return _validation_error(str(exc))

    if "culture_compatibility_enabled" in data:
        enabled = data["culture_compatibility_enabled"]
        if type(enabled) is not bool:
            return _validation_error(
                "culture_compatibility_enabled must be boolean."
            )
        updates["culture_compatibility_enabled"] = enabled

    profile = _m2_current_profile()
    if profile is None:
        return jsonify({
            "error": {
                "code": "PROFILE_UNAVAILABLE",
                "message": "An active completed profile is required.",
            }
        }), 404

    actor = g.current_user["_id"]
    actor = actor if isinstance(actor, ObjectId) else ObjectId(str(actor))

    # Protect the profile's ownership and active status even if it changes
    # between the initial read and the atomic update.
    updates["updated_at"] = datetime.now(timezone.utc)

    result = mongo.db.profiles.update_one(
        {
            "_id": profile["_id"],
            "user_id": actor,
            "onboarding_status": "completed",
            "profile_status": "active",
        },
        {"$set": updates},
    )

    if result.matched_count != 1:
        return jsonify({
            "error": {
                "code": "PROFILE_UNAVAILABLE",
                "message": "An active completed profile is required.",
            }
        }), 404

    saved = mongo.db.profiles.find_one({
        "_id": profile["_id"],
        "user_id": actor,
    })

    if saved is None:
        return jsonify({
            "error": {
                "code": "PROFILE_UNAVAILABLE",
                "message": "Profile unavailable.",
            }
        }), 404

    return jsonify(_m2_profile_view(saved)), 200
