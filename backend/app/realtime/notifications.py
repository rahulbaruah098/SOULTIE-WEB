"""Privacy-scoped realtime notification routing for Xuoroni."""

from flask_socketio import join_room

from app.extensions import socketio
from app.schemas.match_experience import (
    MATCH_CREATED_EVENT,
    build_match_created_payload,
)
from app.repositories.match_repository import (
    list_active_match_peer_ids_for_user,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
from app.repositories.safety_repository import (
    pair_is_blocked,
)
from app.repositories.user_repository import (
    get_user_by_id,
)


PRIVATE_USER_ROOM_PREFIX = (
    "user:"
)

PRESENCE_ONLINE_EVENT = (
    "presence.online"
)

PRESENCE_OFFLINE_EVENT = (
    "presence.offline"
)

CONVERSATION_UPDATED_EVENT = (
    "conversation.updated"
)


ROUTING_ERROR_CODE = (
    "REALTIME_NOTIFICATION_ROUTING_UNAVAILABLE"
)

ROUTING_ERROR_MESSAGE = (
    "Realtime notification routing "
    "is temporarily unavailable."
)


class RealtimeNotificationRoutingError(
    RuntimeError
):
    """Raised when private realtime recipients cannot be resolved safely."""

    def __init__(
        self,
        message=ROUTING_ERROR_MESSAGE,
    ):
        self.code = (
            ROUTING_ERROR_CODE
        )

        self.message = str(
            message
            or ROUTING_ERROR_MESSAGE
        )

        super().__init__(
            self.message
        )


def _normalize_user_id(
    value,
):
    normalized = str(
        value
        or ""
    ).strip()

    if not normalized:
        raise ValueError(
            "User ID cannot be empty."
        )

    return normalized


def private_user_room_name(
    user_id,
):
    """
    Return the server-controlled private room for one user.

    Clients are never given an inbound room-subscription event
    for this namespace.
    """

    return (
        f"{PRIVATE_USER_ROOM_PREFIX}"
        f"{_normalize_user_id(user_id)}"
    )


def join_private_user_room(
    user_id,
):
    """
    Join the current authenticated Socket.IO connection to
    exactly its own private notification room.
    """

    room = private_user_room_name(
        user_id
    )

    join_room(
        room
    )

    return room


def _observer_is_available(
    user_id,
):
    """
    Mirror Chat's peer availability expectations without
    applying discovery visibility rules.

    Hidden-from-discovery profiles may still communicate with
    existing matches, but inactive accounts/profiles may not
    observe realtime presence.
    """

    user = get_user_by_id(
        user_id
    )

    if (
        user is None
        or user.get(
            "account_status"
        )
        != "active"
    ):
        return False

    profile = (
        get_profile_by_user_id(
            user_id
        )
    )

    if (
        profile is None
        or profile.get(
            "onboarding_status"
        )
        != "completed"
        or profile.get(
            "profile_status"
        )
        != "active"
    ):
        return False

    return True


def list_presence_observer_ids(
    user_id,
):
    """
    Resolve users currently permitted to observe this user's
    presence.

    Authorization is recalculated for every transition:
      - active match required,
      - symmetric block check required,
      - observer account/profile must remain usable.

    Any unexpected repository failure aborts the entire lookup
    so presence fails closed rather than leaking to a partial
    or stale recipient set.
    """

    try:
        subject_user_id = (
            _normalize_user_id(
                user_id
            )
        )

        peer_ids = (
            list_active_match_peer_ids_for_user(
                subject_user_id
            )
        )

        observers = []
        seen = set()

        for peer_id in peer_ids:
            observer_user_id = (
                _normalize_user_id(
                    peer_id
                )
            )

            if (
                observer_user_id
                == subject_user_id
            ):
                continue

            if observer_user_id in seen:
                continue

            if pair_is_blocked(
                subject_user_id,
                observer_user_id,
            ):
                continue

            if not _observer_is_available(
                observer_user_id
            ):
                continue

            seen.add(
                observer_user_id
            )

            observers.append(
                observer_user_id
            )

        return observers

    except RealtimeNotificationRoutingError:
        raise

    except Exception as exc:
        raise RealtimeNotificationRoutingError() from exc


def emit_presence_transition(
    *,
    user_id,
    online,
):
    """
    Emit one privacy-scoped presence transition.

    The recipients are resolved server-side from current
    relationship and safety state. No client-supplied room
    or recipient is accepted.
    """

    subject_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    online_value = bool(
        online
    )

    event_name = (
        PRESENCE_ONLINE_EVENT
        if online_value
        else PRESENCE_OFFLINE_EVENT
    )

    observers = (
        list_presence_observer_ids(
            subject_user_id
        )
    )

    payload = {
        "user_id": (
            subject_user_id
        ),
        "online": (
            online_value
        ),
    }

    for observer_user_id in observers:
        socketio.emit(
            event_name,
            payload,
            to=private_user_room_name(
                observer_user_id
            ),
            namespace="/",
        )

    return {
        "event": event_name,
        "recipient_count": len(
            observers
        ),
        "payload": payload,
    }


def emit_match_created(
    *,
    user_id,
    match,
):
    """
    Emit one privacy-safe match.created event.

    The recipient room is derived entirely server-side.
    The match must already be serialized for this recipient,
    so other_user_id always represents the peer rather than
    exposing canonical member IDs.

    This helper does not persist notifications and does not
    create Chat conversations. Those concerns belong to their
    owning subsystems.
    """

    recipient_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    if not isinstance(
        match,
        dict,
    ):
        raise RealtimeNotificationRoutingError()

    try:
        payload = (
            build_match_created_payload(
                match=match
            )
        )

    except ValueError as exc:
        raise RealtimeNotificationRoutingError() from exc

    other_user_id = str(
        payload[
            "match"
        ].get(
            "other_user_id"
        )
        or ""
    ).strip()

    if (
        not other_user_id
        or other_user_id
        == recipient_user_id
    ):
        raise RealtimeNotificationRoutingError()

    try:
        if pair_is_blocked(
            recipient_user_id,
            other_user_id,
        ):
            raise RealtimeNotificationRoutingError()

        if not _observer_is_available(
            recipient_user_id
        ):
            raise RealtimeNotificationRoutingError()

    except RealtimeNotificationRoutingError:
        raise

    except Exception as exc:
        raise RealtimeNotificationRoutingError() from exc

    try:
        socketio.emit(
            MATCH_CREATED_EVENT,
            payload,
            to=private_user_room_name(
                recipient_user_id
            ),
            namespace="/",
        )

    except Exception as exc:
        raise RealtimeNotificationRoutingError() from exc

    return {
        "event": MATCH_CREATED_EVENT,
        "recipient_user_id": (
            recipient_user_id
        ),
        "payload": payload,
    }


def emit_conversation_update(
    *,
    user_id,
    conversation,
    message=None,
    reason="message.new",
):
    """
    Send one server-controlled private conversation update.

    A new-message update may include latest_message. State-only
    updates such as message.read intentionally carry None there.
    The destination user always comes from server-side state.
    """

    recipient_user_id = (
        _normalize_user_id(
            user_id
        )
    )

    if not isinstance(
        conversation,
        dict,
    ):
        raise RealtimeNotificationRoutingError()

    conversation_id = str(
        conversation.get(
            "id"
        )
        or ""
    ).strip()

    reason_value = str(
        reason
        or ""
    ).strip()

    if (
        not conversation_id
        or not reason_value
    ):
        raise RealtimeNotificationRoutingError()

    latest_message = None

    if message is not None:
        if not isinstance(
            message,
            dict,
        ):
            raise RealtimeNotificationRoutingError()

        message_conversation_id = str(
            message.get(
                "conversation_id"
            )
            or ""
        ).strip()

        message_id = str(
            message.get(
                "id"
            )
            or ""
        ).strip()

        if (
            not message_conversation_id
            or not message_id
            or conversation_id
            != message_conversation_id
        ):
            raise RealtimeNotificationRoutingError()

        latest_message = dict(
            message
        )

    payload = {
        "reason": (
            reason_value
        ),
        "conversation": dict(
            conversation
        ),
        "latest_message": (
            latest_message
        ),
    }

    try:
        socketio.emit(
            CONVERSATION_UPDATED_EVENT,
            payload,
            to=private_user_room_name(
                recipient_user_id
            ),
            namespace="/",
        )

    except Exception as exc:
        raise RealtimeNotificationRoutingError() from exc

    return {
        "event": (
            CONVERSATION_UPDATED_EVENT
        ),
        "recipient_user_id": (
            recipient_user_id
        ),
        "payload": payload,
    }




__all__ = [
    "PRESENCE_OFFLINE_EVENT",
    "PRESENCE_ONLINE_EVENT",
    "PRIVATE_USER_ROOM_PREFIX",
    "ROUTING_ERROR_CODE",
    "ROUTING_ERROR_MESSAGE",
    "RealtimeNotificationRoutingError",
    "MATCH_CREATED_EVENT",
    "emit_match_created",
    "emit_presence_transition",
    "join_private_user_room",
    "list_presence_observer_ids",
    "private_user_room_name",
    "CONVERSATION_UPDATED_EVENT",
    "emit_conversation_update",
]
