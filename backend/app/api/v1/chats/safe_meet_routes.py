"""Authenticated Safe Meet endpoints for the Xuoroni chat blueprint."""

from datetime import datetime, timezone
from bson import ObjectId
from flask import g, jsonify, request
from pymongo.errors import PyMongoError

from app.services.safe_meet_service import (
    propose_safe_meet,
    get_authorized_safe_meet,
    transition_authorized_safe_meet,
    get_authorized_safe_meet_events,
)
from app.services.safe_meet_authorization_service import (
    SafeMeetAuthorizationError,
)
from app.services.safe_meet_lifecycle_service import SafeMeetLifecycleError
from app.repositories.safe_meet_repository import (
    SafeMeetConflictError,
    SafeMeetNotFoundError,
)
from app.services.chat_service import ChatServiceError


def _encode(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Naive stored timestamp.")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _error(code, status, message):
    return jsonify({"error": {
        "code": code, "message": message
    }}), status


def _failure(exc):
    if isinstance(exc, SafeMeetConflictError):
        return _error("SAFE_MEET_CONFLICT", 409, "Refresh and retry.")
    if isinstance(exc, SafeMeetNotFoundError):
        return _error("SAFE_MEET_NOT_FOUND", 404, "Plan not found.")
    if isinstance(exc, (
        SafeMeetAuthorizationError, ChatServiceError, PermissionError
    )):
        return _error("SAFE_MEET_UNAVAILABLE", 404, "Plan unavailable.")
    if isinstance(exc, (ValueError, SafeMeetLifecycleError)):
        return _error("SAFE_MEET_INVALID", 400, str(exc))
    if isinstance(exc, PyMongoError):
        return _error(
            "SAFE_MEET_SERVICE_UNAVAILABLE", 503, "Please retry later."
        )
    raise exc


def _actor():
    user = getattr(g, "current_user", None)
    if not isinstance(user, dict) or not isinstance(
        user.get("_id"), ObjectId
    ):
        raise SafeMeetAuthorizationError("Authentication required.")
    return user["_id"]


def _identifier(value):
    if (
        type(value) is not str or len(value) != 24
        or not ObjectId.is_valid(value)
    ):
        raise ValueError("Invalid identifier.")
    return value


def _body(allowed, required):
    if request.content_length is not None and request.content_length > 4096:
        raise ValueError("Request too large.")
    if not request.is_json:
        raise ValueError("JSON body required.")
    data = request.get_json(silent=True)
    if (
        type(data) is not dict
        or not required.issubset(data)
        or not set(data).issubset(allowed)
    ):
        raise ValueError("Unexpected or missing fields.")
    return data


def _planned_time(value):
    if type(value) is not str or len(value) > 50:
        raise ValueError("Invalid planned_at.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid planned_at.") from exc
    if result.tzinfo is None:
        raise ValueError("Timezone is required.")
    return result


def _no_query():
    if request.args:
        raise ValueError("Unexpected query parameters.")


def register_safe_meet_routes(blueprint, auth_required):
    @blueprint.post("/<conversation_id>/safe-meets")
    @auth_required
    def safe_meet_propose_api(conversation_id):
        try:
            actor = _actor()
            _no_query()
            conversation_id = _identifier(conversation_id)
            data = _body(
                {"planned_at", "venue_id", "reservation_id"},
                {"planned_at"},
            )
            venue = data.get("venue_id")
            reservation = data.get("reservation_id")
            if venue is not None:
                venue = _identifier(venue)
            if reservation is not None:
                reservation = _identifier(reservation)
            plan, event = propose_safe_meet(
                user_id=actor,
                conversation_id=conversation_id,
                planned_at=_planned_time(data["planned_at"]),
                venue_id=venue,
                reservation_id=reservation,
            )
            return jsonify({
                "safe_meet": _encode(plan),
                "event": _encode(event),
            }), 201
        except (
            ValueError, SafeMeetAuthorizationError, ChatServiceError,
            PermissionError, SafeMeetLifecycleError,
            SafeMeetConflictError, SafeMeetNotFoundError, PyMongoError,
        ) as exc:
            return _failure(exc)

    @blueprint.get("/safe-meets/<safe_meet_id>")
    @auth_required
    def safe_meet_detail_api(safe_meet_id):
        try:
            actor = _actor()
            _no_query()
            plan = get_authorized_safe_meet(
                _identifier(safe_meet_id), user_id=actor
            )
            return jsonify({"safe_meet": _encode(plan)})
        except (
            ValueError, SafeMeetAuthorizationError, ChatServiceError,
            PermissionError, SafeMeetLifecycleError,
            SafeMeetConflictError, SafeMeetNotFoundError, PyMongoError,
        ) as exc:
            return _failure(exc)

    @blueprint.post("/safe-meets/<safe_meet_id>/actions")
    @auth_required
    def safe_meet_action_api(safe_meet_id):
        try:
            actor = _actor()
            _no_query()
            safe_meet_id = _identifier(safe_meet_id)
            data = _body(
                {"action", "expected_revision"},
                {"action", "expected_revision"},
            )
            action = data["action"]
            if type(action) is not str or action not in {
                "accept", "decline", "cancel", "complete"
            }:
                raise ValueError("Invalid action.")
            revision = data["expected_revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("Invalid expected revision.")
            plan, event = transition_authorized_safe_meet(
                safe_meet_id,
                user_id=actor,
                action=action,
                expected_revision=revision,
            )
            return jsonify({
                "safe_meet": _encode(plan),
                "event": _encode(event),
            })
        except (
            ValueError, SafeMeetAuthorizationError, ChatServiceError,
            PermissionError, SafeMeetLifecycleError,
            SafeMeetConflictError, SafeMeetNotFoundError, PyMongoError,
        ) as exc:
            return _failure(exc)

    @blueprint.get("/safe-meets/<safe_meet_id>/events")
    @auth_required
    def safe_meet_events_api(safe_meet_id):
        try:
            actor = _actor()
            _no_query()
            events = get_authorized_safe_meet_events(
                _identifier(safe_meet_id), user_id=actor
            )
            return jsonify({"events": _encode(events)})
        except (
            ValueError, SafeMeetAuthorizationError, ChatServiceError,
            PermissionError, SafeMeetLifecycleError,
            SafeMeetConflictError, SafeMeetNotFoundError, PyMongoError,
        ) as exc:
            return _failure(exc)


    @blueprint.get("/safe-meets/<safe_meet_id>/booking-health")
    @auth_required
    def safe_meet_booking_health_api(safe_meet_id):
        try:
            actor = _actor()
            _no_query()
            safe_meet_id = _identifier(safe_meet_id)

            from app.services.safe_meet_reconciliation_service import (
                read_authorized_safe_meet_booking_health,
            )

            health = read_authorized_safe_meet_booking_health(
                safe_meet_id,
                user_id=actor,
            )
            return jsonify({"booking_health": _encode(health)})

        except (
            ValueError,
            SafeMeetAuthorizationError,
            ChatServiceError,
            PermissionError,
            SafeMeetLifecycleError,
            SafeMeetConflictError,
            SafeMeetNotFoundError,
            PyMongoError,
        ) as exc:
            return _failure(exc)
