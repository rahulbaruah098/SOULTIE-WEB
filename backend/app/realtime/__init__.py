"""Authenticated Socket.IO boundary for Soultie realtime features."""

from flask import (
    current_app,
    request,
    session,
)
from flask_socketio import (
    ConnectionRefusedError,
)

from app.extensions import (
    socketio,
)
from app.realtime.auth import (
    RealtimeAuthError,
    authenticate_realtime_connection,
    validate_realtime_identity,
)
from app.realtime.notifications import (
    RealtimeNotificationRoutingError,
    emit_presence_transition,
    join_private_user_room,
)
from app.realtime.presence import (
    PresenceStateError,
    mark_socket_online,
    refresh_socket_presence,
    remove_socket_presence,
)


REALTIME_USER_SESSION_KEY = (
    "xuoroni_realtime_user_id"
)

REALTIME_AUTH_SESSION_KEY = (
    "xuoroni_realtime_session_id"
)

PRESENCE_HEARTBEAT_EVENT = (
    "presence.heartbeat"
)


def _realtime_error_payload(
    error,
):
    return {
        "code": error.code,
        "message": error.message,
    }


def _presence_error_payload(
    error,
):
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
        },
    }


def _log_presence_failure(
    operation,
    error,
):
    current_app.logger.warning(
        (
            "Realtime presence operation failed: "
            "%s"
        ),
        operation,
        extra={
            "presence_error_code": getattr(
                error,
                "code",
                "REALTIME_STATE_UNAVAILABLE",
            ),
        },
    )


def _log_notification_failure(
    operation,
    error,
):
    current_app.logger.warning(
        (
            "Realtime notification operation failed: "
            "%s"
        ),
        operation,
        extra={
            "notification_error_code": getattr(
                error,
                "code",
                (
                    "REALTIME_NOTIFICATION_"
                    "ROUTING_UNAVAILABLE"
                ),
            ),
        },
    )


def clear_realtime_identity():
    session.pop(
        REALTIME_USER_SESSION_KEY,
        None,
    )

    session.pop(
        REALTIME_AUTH_SESSION_KEY,
        None,
    )


def bind_realtime_identity(
    identity,
):
    user_id = identity.get(
        "user_id"
    )

    session_id = identity.get(
        "session_id"
    )

    if (
        user_id is None
        or not isinstance(
            session_id,
            str,
        )
        or not session_id.strip()
    ):
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    session[
        REALTIME_USER_SESSION_KEY
    ] = str(
        user_id
    )

    session[
        REALTIME_AUTH_SESSION_KEY
    ] = session_id.strip()


def get_realtime_identity():
    """
    Return and revalidate the authenticated identity
    currently bound to this Socket.IO connection.

    Every protected realtime event should call this
    before performing application work.
    """

    user_id = session.get(
        REALTIME_USER_SESSION_KEY
    )

    session_id = session.get(
        REALTIME_AUTH_SESSION_KEY
    )

    if (
        not user_id
        or not session_id
    ):
        raise RealtimeAuthError(
            "REALTIME_AUTHENTICATION_REQUIRED",
            (
                "A valid realtime session "
                "is required."
            ),
        )

    return validate_realtime_identity(
        user_id=user_id,
        session_id=session_id,
    )


@socketio.on(
    "connect"
)
def handle_realtime_connect(
    auth,
):
    """
    Authenticate a Socket.IO connection.

    Authentication is performed only from the supplied
    Soultie access token. Client-supplied user IDs are
    never trusted.

    Presence is best-effort ephemeral state. A temporary
    presence-store failure must not reject an otherwise
    valid authenticated connection.
    """

    clear_realtime_identity()

    try:
        identity = (
            authenticate_realtime_connection(
                auth
            )
        )

        bind_realtime_identity(
            identity
        )

    except RealtimeAuthError as exc:
        clear_realtime_identity()

        raise ConnectionRefusedError(
            _realtime_error_payload(
                exc
            )
        ) from None

    try:
        join_private_user_room(
            identity[
                "user_id"
            ]
        )

    except Exception as exc:
        # Private notifications are supplementary realtime
        # delivery. A room-routing failure must not invalidate
        # an otherwise authenticated Chat connection.
        _log_notification_failure(
            "private_room_join",
            exc,
        )

    presence_state = None

    try:
        presence_state = (
            mark_socket_online(
                user_id=identity[
                    "user_id"
                ],
                socket_id=request.sid,
            )
        )

    except PresenceStateError as exc:
        _log_presence_failure(
            "connect",
            exc,
        )

    if (
        isinstance(
            presence_state,
            dict,
        )
        and presence_state.get(
            "changed"
        )
        is True
    ):
        try:
            emit_presence_transition(
                user_id=identity[
                    "user_id"
                ],
                online=True,
            )

        except RealtimeNotificationRoutingError as exc:
            _log_notification_failure(
                "presence_online_connect",
                exc,
            )

    return True


@socketio.on(
    PRESENCE_HEARTBEAT_EVENT
)
def handle_presence_heartbeat(
    payload=None,
):
    """
    Refresh this authenticated socket's presence TTL.

    The user identity always comes from the authenticated
    Socket.IO session, never from client payload fields.
    """

    try:
        identity = (
            get_realtime_identity()
        )

    except RealtimeAuthError as exc:
        return {
            "ok": False,
            "error": (
                _realtime_error_payload(
                    exc
                )
            ),
        }

    try:
        state = (
            refresh_socket_presence(
                user_id=identity[
                    "user_id"
                ],
                socket_id=request.sid,
            )
        )

    except PresenceStateError as exc:
        return _presence_error_payload(
            exc
        )

    if (
        isinstance(
            state,
            dict,
        )
        and state.get(
            "changed"
        )
        is True
    ):
        try:
            emit_presence_transition(
                user_id=identity[
                    "user_id"
                ],
                online=True,
            )

        except RealtimeNotificationRoutingError as exc:
            _log_notification_failure(
                "presence_online_heartbeat",
                exc,
            )

    return {
        "ok": True,
        **state,
    }


@socketio.on(
    "disconnect"
)
def handle_realtime_disconnect(
    reason=None,
):
    """
    Remove this socket's ephemeral presence marker and then
    clear connection-scoped authentication state.

    Disconnect cleanup does not revalidate the login session:
    a revoked/expired login can still own a stale presence key
    that must be removed.

    Presence failure is non-fatal. Authentication state is
    always cleared.
    """

    user_id = session.get(
        REALTIME_USER_SESSION_KEY
    )

    try:
        presence_state = None

        if user_id:
            try:
                presence_state = (
                    remove_socket_presence(
                        user_id=user_id,
                        socket_id=request.sid,
                    )
                )

            except PresenceStateError as exc:
                _log_presence_failure(
                    "disconnect",
                    exc,
                )

            if (
                isinstance(
                    presence_state,
                    dict,
                )
                and presence_state.get(
                    "changed"
                )
                is True
            ):
                try:
                    emit_presence_transition(
                        user_id=user_id,
                        online=False,
                    )

                except RealtimeNotificationRoutingError as exc:
                    _log_notification_failure(
                        "presence_offline_disconnect",
                        exc,
                    )

    finally:
        clear_realtime_identity()


# Register conversation-specific realtime handlers only after
# the connection identity helpers above are fully defined.
from app.realtime import chat as _chat


__all__ = [
    "PRESENCE_HEARTBEAT_EVENT",
    "REALTIME_AUTH_SESSION_KEY",
    "REALTIME_USER_SESSION_KEY",
    "bind_realtime_identity",
    "clear_realtime_identity",
    "get_realtime_identity",
    "handle_presence_heartbeat",
    "handle_realtime_connect",
    "handle_realtime_disconnect",
]
