"""Authenticated Safe Meet endpoints for the Soultie chat blueprint."""

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

    # SAFE_MEET_CHECKIN_API_CP14B

    @blueprint.post("/safe-meets/<safe_meet_id>/check-ins/arrival")
    @auth_required
    def safe_meet_arrival_api(safe_meet_id):
        try:
            from datetime import datetime, timezone
            from app.extensions import mongo
            from app.services.safe_meet_checkin_service import (
                perform_safe_meet_checkin,
            )

            actor = _actor()
            _no_query()
            safe_meet_id = _identifier(safe_meet_id)

            # Check the existing authorization policy before writing.
            get_authorized_safe_meet(safe_meet_id, user_id=actor)
            _body(set(), set())

            result = perform_safe_meet_checkin(
                database=mongo.db,
                safe_meet_id=safe_meet_id,
                participant_id=str(actor),
                action="arrived",
                now=datetime.now(timezone.utc),
            )
            return jsonify({"check_in": _encode(result)}), 201
        except (ValueError, PermissionError, PyMongoError,
                SafeMeetAuthorizationError, SafeMeetNotFoundError,
                SafeMeetLifecycleError, SafeMeetConflictError,
                ChatServiceError) as exc:
            return _failure(exc)


    @blueprint.post("/safe-meets/<safe_meet_id>/check-ins/safe-departure")
    @auth_required
    def safe_meet_departure_api(safe_meet_id):
        try:
            from datetime import datetime, timezone
            from app.extensions import mongo
            from app.services.safe_meet_checkin_service import (
                perform_safe_meet_checkin,
            )

            actor = _actor()
            _no_query()
            safe_meet_id = _identifier(safe_meet_id)
            get_authorized_safe_meet(safe_meet_id, user_id=actor)
            _body(set(), set())

            result = perform_safe_meet_checkin(
                database=mongo.db,
                safe_meet_id=safe_meet_id,
                participant_id=str(actor),
                action="left_safely",
                now=datetime.now(timezone.utc),
            )
            return jsonify({"check_in": _encode(result)})
        except (ValueError, PermissionError, PyMongoError,
                SafeMeetAuthorizationError, SafeMeetNotFoundError,
                SafeMeetLifecycleError, SafeMeetConflictError,
                ChatServiceError) as exc:
            return _failure(exc)


    @blueprint.get("/safe-meets/<safe_meet_id>/check-ins/me")
    @auth_required
    def safe_meet_my_checkin_api(safe_meet_id):
        try:
            from app.extensions import mongo

            actor = _actor()
            _no_query()
            safe_meet_id = _identifier(safe_meet_id)
            get_authorized_safe_meet(safe_meet_id, user_id=actor)

            state = mongo.db.safe_meet_checkins.find_one(
                {
                    "safe_meet_id": safe_meet_id,
                    "participant_id": str(actor),
                },
                {"_id": 0},
            )
            events = list(
                mongo.db.safe_meet_checkin_events.find(
                    {
                        "safe_meet_id": safe_meet_id,
                        "participant_id": str(actor),
                    },
                    {"_id": 0},
                ).sort("sequence", 1)
            )

            from datetime import datetime as _sm_history_datetime
            from datetime import timezone as _sm_history_timezone

            def _sm_history_utc(value):
                if isinstance(value, _sm_history_datetime):
                    if value.tzinfo is None:
                        return value.replace(tzinfo=_sm_history_timezone.utc)
                    return value
                if isinstance(value, dict):
                    return {
                        key: _sm_history_utc(item)
                        for key, item in value.items()
                    }
                if isinstance(value, list):
                    return [_sm_history_utc(item) for item in value]
                return value

            state = _sm_history_utc(state)
            events = _sm_history_utc(events)

            return jsonify({
                "check_in": _encode(state),
                "events": _encode(events),
            })
        except (ValueError, PermissionError, PyMongoError,
                SafeMeetAuthorizationError, SafeMeetNotFoundError,
                SafeMeetLifecycleError, SafeMeetConflictError,
                ChatServiceError) as exc:
            return _failure(exc)

    # SAFE_MEET_TRUSTED_CONTACT_API_CP15C
    @blueprint.post("/safe-meets/<safe_meet_id>/trusted-contact/consent")
    @auth_required
    def safe_meet_contact_grant_api(safe_meet_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_trusted_contact_consent_service import (
            grant_trusted_contact_consent, SafeMeetConsentError,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            data = _body(
                {"contact_id", "expires_at", "location_enabled"},
                {"contact_id", "expires_at", "location_enabled"},
            )
            get_authorized_safe_meet(meet, user_id=actor)
            expiry = datetime.fromisoformat(data["expires_at"])
            if expiry.tzinfo is None:
                raise ValueError("Timezone required.")
            result = grant_trusted_contact_consent(
                database=mongo.db,
                safe_meet_id=meet,
                owner_id=actor,
                contact_id=_identifier(data["contact_id"]),
                expires_at=expiry,
                location_enabled=data["location_enabled"],
                now=datetime.now(timezone.utc),
            )
            return jsonify({"consent": result}), 201
        except (ValueError, TypeError, SafeMeetConsentError) as exc:
            return jsonify({"error": {
                "code": "TRUSTED_CONTACT_INVALID",
                "message": str(exc),
            }}), 400
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404

    @blueprint.post("/safe-meets/<safe_meet_id>/trusted-contact/revoke")
    @auth_required
    def safe_meet_contact_revoke_api(safe_meet_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_trusted_contact_consent_service import (
            revoke_trusted_contact_consent, SafeMeetConsentError,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            _body(set(), set())
            get_authorized_safe_meet(meet, user_id=actor)
            result = revoke_trusted_contact_consent(
                database=mongo.db, safe_meet_id=meet,
                owner_id=actor, now=datetime.now(timezone.utc),
            )
            return jsonify({"consent": result})
        except (ValueError, TypeError, SafeMeetConsentError) as exc:
            return jsonify({"error": {
                "code": "TRUSTED_CONTACT_INVALID",
                "message": str(exc),
            }}), 400
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404

    @blueprint.post("/safe-meets/<safe_meet_id>/trusted-contact/accept")
    @auth_required
    def safe_meet_contact_accept_api(safe_meet_id):
        return _safe_meet_contact_response(safe_meet_id, "accept")

    @blueprint.post("/safe-meets/<safe_meet_id>/trusted-contact/reject")
    @auth_required
    def safe_meet_contact_reject_api(safe_meet_id):
        return _safe_meet_contact_response(safe_meet_id, "reject")

    def _safe_meet_contact_response(safe_meet_id, decision):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_trusted_contact_acceptance_service import (
            respond_to_trusted_contact_invitation,
            TrustedContactAcceptanceError,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            data = _body({"owner_id"}, {"owner_id"})
            result = respond_to_trusted_contact_invitation(
                database=mongo.db,
                safe_meet_id=meet,
                owner_id=_identifier(data["owner_id"]),
                contact_id=actor,
                decision=decision,
                now=datetime.now(timezone.utc),
            )
            return jsonify({"response": result})
        except (ValueError, TypeError, TrustedContactAcceptanceError):
            return jsonify({"error": {
                "code": "INVITATION_UNAVAILABLE",
                "message": "Invitation unavailable.",
            }}), 404

    @blueprint.get("/safe-meets/<safe_meet_id>/trusted-contact/me")
    @auth_required
    def safe_meet_contact_me_api(safe_meet_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_trusted_contact_consent_service import (
            read_active_trusted_contact_consent,
        )
        from app.services.safe_meet_trusted_contact_acceptance_service import (
            get_verified_trusted_contact,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            get_authorized_safe_meet(meet, user_id=actor)
            now = datetime.now(timezone.utc)
            active = read_active_trusted_contact_consent(
                database=mongo.db, safe_meet_id=meet,
                owner_id=actor, now=now,
            )
            verified = get_verified_trusted_contact(
                database=mongo.db, safe_meet_id=meet,
                owner_id=actor, now=now,
            )
            return jsonify({"consent": {
                "status": "active" if active else "none",
                "contact_id": str(active["contact_id"]) if active else None,
                "location_enabled": (
                    active["location_enabled"] is True if active else False
                ),
                "accepted": verified is not None,
            }})
        except (ValueError, TypeError):
            return jsonify({"error": {
                "code": "INVALID_MEETING",
                "message": "Invalid meeting.",
            }}), 400
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404

    # SAFE_MEET_LOCATION_API_CP15F
    @blueprint.post("/safe-meets/<safe_meet_id>/trusted-contact/location")
    @auth_required
    def safe_meet_location_publish_api(safe_meet_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_location_privacy_service import (
            publish_location_snapshot, SafeMeetLocationError,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            data = _body(
                {"latitude", "longitude"},
                {"latitude", "longitude"},
            )
            get_authorized_safe_meet(meet, user_id=actor)
            result = publish_location_snapshot(
                database=mongo.db,
                safe_meet_id=meet,
                owner_id=actor,
                latitude=data["latitude"],
                longitude=data["longitude"],
                now=datetime.now(timezone.utc),
            )
            return jsonify({
                "status": result["status"],
                "expires_at": result["expires_at"].isoformat(),
            }), 201
        except (ValueError, TypeError, SafeMeetLocationError):
            return jsonify({"error": {
                "code": "LOCATION_UNAVAILABLE",
                "message": "Location update unavailable.",
            }}), 400
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404

    @blueprint.get("/safe-meets/<safe_meet_id>/trusted-contact/location/<owner_id>")
    @auth_required
    def safe_meet_location_contact_read_api(safe_meet_id, owner_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_location_privacy_service import (
            read_location_for_contact, SafeMeetLocationError,
        )
        try:
            actor = _actor()
            _no_query()
            meet = _identifier(safe_meet_id)
            owner = _identifier(owner_id)

            # Trusted contacts may be outside the conversation.
            # Do not use conversation membership as their credential.
            # The accepted invitation is their authorization.
            result = read_location_for_contact(
                database=mongo.db,
                safe_meet_id=meet,
                owner_id=owner,
                requester_id=actor,
                now=datetime.now(timezone.utc),
            )
            if result is None:
                return jsonify({"location": None})

            def utc_iso(value):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc).isoformat()

            return jsonify({"location": {
                "latitude": result["latitude"],
                "longitude": result["longitude"],
                "captured_at": utc_iso(result["captured_at"]),
                "expires_at": utc_iso(result["expires_at"]),
            }})
        except (ValueError, TypeError, SafeMeetLocationError):
            return jsonify({"error": {
                "code": "LOCATION_UNAVAILABLE",
                "message": "Location unavailable.",
            }}), 404


    # SAFE_MEET_SOS_API_CP16B
    @blueprint.post("/safe-meets/<safe_meet_id>/sos")
    @auth_required
    def safe_meet_sos_trigger_api(safe_meet_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_sos_service import (
            trigger_safe_meet_sos, SafeMeetSOSError,
        )
        try:
            actor = _actor()
            _no_query()
            meeting = _identifier(safe_meet_id)
            body = _body(
                {"idempotency_key"},
                {"idempotency_key"},
            )

            # Existing Safe Meet authorization remains mandatory.
            get_authorized_safe_meet(meeting, user_id=actor)

            result = trigger_safe_meet_sos(
                database=mongo.db,
                safe_meet_id=meeting,
                owner_id=actor,
                idempotency_key=body["idempotency_key"],
                now=datetime.now(timezone.utc),
            )
            # Recording an SOS does not establish delivery.
            return jsonify({"sos": result}), (
                200 if result["replayed"] else 201
            )
        except (ValueError, TypeError, SafeMeetSOSError):
            return jsonify({"error": {
                "code": "SOS_INVALID",
                "message": "SOS request unavailable.",
            }}), 400
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404

    @blueprint.get("/safe-meets/<safe_meet_id>/sos/<sos_event_id>")
    @auth_required
    def safe_meet_sos_status_api(safe_meet_id, sos_event_id):
        from app.extensions import mongo
        try:
            actor = _actor()
            _no_query()
            meeting = _identifier(safe_meet_id)
            event_id = _identifier(sos_event_id)
            get_authorized_safe_meet(meeting, user_id=actor)

            # Read is restricted to the SOS creator, not merely
            # any participant of the conversation.
            event = mongo.db.safe_meet_sos_events.find_one({
                "_id": ObjectId(event_id),
                "safe_meet_id": ObjectId(meeting),
                "owner_id": actor,
            })
            if event is None:
                return jsonify({"error": {
                    "code": "SOS_UNAVAILABLE",
                    "message": "SOS unavailable.",
                }}), 404

            return jsonify({"sos": {
                "sos_event_id": str(event["_id"]),
                "status": event["status"],
                "escalation_status": event["escalation_status"],
                "created_at": (
                    event["created_at"].replace(tzinfo=__import__(
                        "datetime"
                    ).timezone.utc).isoformat()
                    if event["created_at"].tzinfo is None
                    else event["created_at"].isoformat()
                ),
                "delivery_confirmed": False,
            }})
        except (ValueError, TypeError):
            return jsonify({"error": {
                "code": "SOS_UNAVAILABLE",
                "message": "SOS unavailable.",
            }}), 404
        except (SafeMeetAuthorizationError, SafeMeetNotFoundError,
                ChatServiceError):
            return jsonify({"error": {
                "code": "SAFE_MEET_UNAVAILABLE",
                "message": "Meeting unavailable.",
            }}), 404


    # SAFE_MEET_TRUSTED_CONTACT_SOS_INBOX_API
    @blueprint.get("/safe-meets/trusted-contact/sos-inbox")
    @auth_required
    def trusted_contact_sos_inbox_api():
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_sos_inbox_service import (
            list_trusted_contact_sos,
        )
        try:
            actor = _actor()
            _no_query()
            items = list_trusted_contact_sos(
                database=mongo.db,
                contact_id=actor,
                now=datetime.now(timezone.utc),
                limit=20,
            )
            return jsonify({
                "items": items,
                "count": len(items),
                "delivery_confirmed": False,
            }), 200
        except (ValueError, TypeError):
            return jsonify({
                "error": {
                    "code": "SOS_INBOX_UNAVAILABLE",
                    "message": "SOS inbox unavailable.",
                }
            }), 400


    # SAFE_MEET_SOS_ACKNOWLEDGMENT_API
    @blueprint.post(
        "/safe-meets/trusted-contact/sos-inbox/<sos_event_id>/acknowledge"
    )
    @auth_required
    def trusted_contact_sos_acknowledge_api(sos_event_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_sos_acknowledgment_service import (
            acknowledge_contact_sos,
            SOSAcknowledgmentUnavailable,
        )
        try:
            actor = _actor()
            _no_query()
            event_id = _identifier(sos_event_id)
            body = _body(set(), set())
            result = acknowledge_contact_sos(
                database=mongo.db,
                sos_event_id=event_id,
                contact_id=actor,
                now=datetime.now(timezone.utc),
            )
            return jsonify({"acknowledgment": result}), (
                200 if result["replayed"] else 201
            )
        except (ValueError, TypeError, SOSAcknowledgmentUnavailable):
            return jsonify({
                "error": {
                    "code": "SOS_UNAVAILABLE",
                    "message": "SOS unavailable.",
                }
            }), 404


    # SAFE_MEET_SOS_OWNER_ACK_STATUS_API
    @blueprint.get(
        "/safe-meets/<safe_meet_id>/sos/<event_id>/acknowledgments"
    )
    @auth_required
    def safe_meet_owner_sos_ack_status_api(safe_meet_id, event_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_sos_owner_status_service import (
            get_owner_sos_acknowledgment_status,
            OwnerSOSUnavailable,
        )
        try:
            actor = _actor()
            _no_query()
            result = get_owner_sos_acknowledgment_status(
                database=mongo.db,
                safe_meet_id=safe_meet_id,
                sos_event_id=event_id,
                owner_id=actor,
                now=datetime.now(timezone.utc),
            )
            return jsonify({"acknowledgment": result}), 200
        except (ValueError, TypeError, OwnerSOSUnavailable):
            return jsonify({
                "error": {
                    "code": "SOS_UNAVAILABLE",
                    "message": "SOS unavailable.",
                }
            }), 404


    # SAFE_MEET_SOS_OWNER_CLOSURE_API
    @blueprint.post(
        "/safe-meets/<safe_meet_id>/sos/<event_id>/close"
    )
    @auth_required
    def safe_meet_owner_close_sos_api(safe_meet_id, event_id):
        from datetime import datetime, timezone
        from app.extensions import mongo
        from app.services.safe_meet_sos_closure_service import (
            close_owner_sos,
            SOSClosureUnavailable,
        )
        try:
            actor = _actor()
            _no_query()
            _body(set(), set())
            result = close_owner_sos(
                database=mongo.db,
                safe_meet_id=safe_meet_id,
                sos_event_id=event_id,
                owner_id=actor,
                now=datetime.now(timezone.utc),
            )
            return jsonify({"closure": result}), 200
        except (ValueError, TypeError, SOSClosureUnavailable):
            return jsonify({
                "error": {
                    "code": "SOS_UNAVAILABLE",
                    "message": "SOS unavailable.",
                }
            }), 404


    # SOULTIE_MATCHING_COMPATIBILITY_M1
    @blueprint.get("/<conversation_id>/compatibility")
    @auth_required
    def conversation_culture_compatibility_api(conversation_id):
        from app.extensions import mongo
        from app.services.culture_conversation_service import (
            conversation_compatibility,
            CompatibilityUnavailable,
        )
        try:
            actor = _actor()
            _no_query()
            result = conversation_compatibility(
                database=mongo.db,
                actor_id=actor,
                conversation_id=conversation_id,
            )
            return jsonify(result), 200
        except (CompatibilityUnavailable, ValueError, TypeError):
            return jsonify({
                "error": {
                    "code": "COMPATIBILITY_UNAVAILABLE",
                    "message": "Compatibility unavailable.",
                }
            }), 404

