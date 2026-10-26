"""Authorized realtime chat-room handlers for Soultie."""

from flask import (
    current_app,
    request,
)
from flask_socketio import (
    join_room,
    leave_room,
)
from redis.exceptions import RedisError

from app.extensions import (
    redis_store,
    socketio,
)
from app.realtime import (
    get_realtime_identity,
)
from app.realtime.auth import (
    RealtimeAuthError,
)
from app.realtime.notifications import (
    ROUTING_ERROR_CODE,
    ROUTING_ERROR_MESSAGE,
    RealtimeNotificationRoutingError,
    emit_conversation_update,
    list_presence_observer_ids,
)
from app.realtime.presence import (
    PresenceStateError,
    is_user_online,
)
from app.repositories.profile_repository import (
    get_profile_by_user_id,
)
from app.repositories.user_repository import (
    get_user_by_id,
)
from app.services.chat_service import (
    ChatServiceError,
    get_my_conversation,
    mark_chat_delivered,
    mark_chat_read,
    send_chat_message,
)


CHAT_ROOM_PREFIX = (
    "chat:conversation:"
)

MESSAGE_SEND_EVENT = "message.send"
MESSAGE_NEW_EVENT = "message.new"
MESSAGE_DELIVERED_EVENT = "message.delivered"
MESSAGE_READ_EVENT = "message.read"

TYPING_START_EVENT = "typing.start"
TYPING_STOP_EVENT = "typing.stop"


PRESENCE_GET_EVENT = "presence.get"


def conversation_room_name(
    conversation_id,
):
    """
    Build the internal Socket.IO room name for a
    server-validated conversation ID.
    """

    value = str(
        conversation_id
    ).strip()

    if not value:
        raise ValueError(
            (
                "Conversation ID cannot "
                "be empty."
            )
        )

    return (
        CHAT_ROOM_PREFIX
        + value
    )


def _typing_key_base(
    *,
    conversation_id,
    user_id,
):
    prefix = str(
        current_app.config.get(
            "REALTIME_TYPING_KEY_PREFIX",
            "xuoroni:realtime:typing",
        )
        or ""
    ).strip()

    if not prefix:
        raise RuntimeError(
            (
                "Realtime typing key "
                "prefix is not configured."
            )
        )

    return (
        f"{prefix}:"
        f"{str(conversation_id).strip()}:"
        f"{str(user_id).strip()}"
    )


def typing_state_key(
    *,
    conversation_id,
    user_id,
    socket_id,
):
    base = _typing_key_base(
        conversation_id=(
            conversation_id
        ),
        user_id=user_id,
    )

    socket_value = str(
        socket_id
        or ""
    ).strip()

    if not socket_value:
        raise ValueError(
            (
                "Socket ID cannot "
                "be empty."
            )
        )

    return (
        f"{base}:{socket_value}"
    )


def _typing_ttl_seconds():
    try:
        ttl = int(
            current_app.config.get(
                "REALTIME_TYPING_TTL_SECONDS",
                8,
            )
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            (
                "Realtime typing TTL "
                "is invalid."
            )
        ) from exc

    if ttl <= 0:
        raise RuntimeError(
            (
                "Realtime typing TTL "
                "must be positive."
            )
        )

    return ttl


def _typing_redis_error():
    return _error_payload(
        code="REALTIME_STATE_UNAVAILABLE",
        message=(
            "Realtime typing state "
            "is temporarily unavailable."
        ),
    )


def _error_payload(
    *,
    code,
    message,
):
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _validation_error(
    message,
):
    return _error_payload(
        code=(
            "CHAT_VALIDATION_ERROR"
        ),
        message=message,
    )


def _auth_error(
    error,
):
    return _error_payload(
        code=error.code,
        message=error.message,
    )


def _service_error(
    error,
):
    return _error_payload(
        code=error.code,
        message=error.message,
    )


def _require_event_payload(
    payload,
):
    if not isinstance(
        payload,
        dict,
    ):
        return (
            None,
            _validation_error(
                (
                    "A realtime event "
                    "payload is required."
                )
            ),
        )

    conversation_id = (
        payload.get(
            "conversation_id"
        )
    )

    if (
        not isinstance(
            conversation_id,
            str,
        )
        or not conversation_id.strip()
    ):
        return (
            None,
            _validation_error(
                (
                    "conversation_id "
                    "is required."
                )
            ),
        )

    return (
        conversation_id.strip(),
        None,
    )


def _authorized_conversation(
    conversation_id,
):
    """
    Revalidate the socket identity and then delegate
    conversation membership authorization to the
    existing Chat service.
    """

    identity = (
        get_realtime_identity()
    )

    conversation = (
        get_my_conversation(
            user_id=identity[
                "user_id"
            ],
            conversation_id=(
                conversation_id
            ),
        )
    )

    return (
        identity,
        conversation,
    )


@socketio.on(
    "chat:join"
)
def handle_chat_join(
    payload,
):
    """
    Join the authenticated socket to one conversation room.

    The client supplies only the conversation ID. User identity
    always comes from the authenticated Socket.IO connection.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        (
            _identity,
            conversation,
        ) = _authorized_conversation(
            conversation_id
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    room = conversation_room_name(
        conversation[
            "id"
        ]
    )

    join_room(
        room
    )

    return {
        "ok": True,
        "conversation": (
            conversation
        ),
    }


@socketio.on(
    "chat:leave"
)
def handle_chat_leave(
    payload,
):
    """
    Leave one authorized conversation room.

    Membership is checked before deriving the room name so a
    client cannot use this event as a conversation-ID oracle.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        (
            _identity,
            conversation,
        ) = _authorized_conversation(
            conversation_id
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    room = conversation_room_name(
        conversation[
            "id"
        ]
    )

    leave_room(
        room
    )

    return {
        "ok": True,
        "conversation_id": (
            conversation[
                "id"
            ]
        ),
    }


@socketio.on(
    MESSAGE_SEND_EVENT
)
def handle_message_send(
    payload,
):
    """
    Persist one authenticated Chat message and broadcast
    a newly-created message to the authorized conversation room.

    The authenticated socket identity is always authoritative.
    Any client-supplied user_id or sender_id is ignored.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        identity = (
            get_realtime_identity()
        )

        result = (
            send_chat_message(
                user_id=identity[
                    "user_id"
                ],
                conversation_id=(
                    conversation_id
                ),
                client_message_id=(
                    payload.get(
                        "client_message_id"
                    )
                ),
                body=payload.get(
                    "body"
                ),
                message_type=(
                    payload.get(
                        "message_type",
                        "text",
                    )
                ),
            )
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    if not isinstance(
        result,
        dict,
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Chat message state "
                "is invalid."
            ),
        )

    message = result.get(
        "message"
    )

    if (
        not isinstance(
            message,
            dict,
        )
        or not isinstance(
            message.get(
                "conversation_id"
            ),
            str,
        )
        or not message.get(
            "conversation_id"
        ).strip()
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Chat message state "
                "is invalid."
            ),
        )

    # Only a genuinely new durable message is broadcast.
    # An idempotent retry receives its ACK below but must
    # not generate a duplicate message.new event.
    if (
        result.get(
            "created"
        )
        is True
    ):
        room = (
            conversation_room_name(
                message[
                    "conversation_id"
                ]
            )
        )

        socketio.emit(
            MESSAGE_NEW_EVENT,
            {
                "message": message,
            },
            to=room,
            namespace="/",
        )

        _emit_recipient_conversation_update(
            message
        )

    return {
        "ok": True,
        **result,
    }


def _handle_message_receipt(
    payload,
    *,
    service_function,
    event_name,
):
    """
    Persist one authenticated delivery/read receipt.

    Durable state is updated by the Chat service first.
    Realtime broadcast is only a notification of that
    already-persisted state.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        identity = (
            get_realtime_identity()
        )

        result = service_function(
            user_id=identity[
                "user_id"
            ],
            conversation_id=(
                conversation_id
            ),
            through_sequence=(
                payload.get(
                    "through_sequence"
                )
            ),
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    if not isinstance(
        result,
        dict,
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Chat receipt state "
                "is invalid."
            ),
        )

    through_sequence = (
        result.get(
            "through_sequence"
        )
    )

    modified_count = (
        result.get(
            "modified_count"
        )
    )

    if (
        isinstance(
            through_sequence,
            bool,
        )
        or not isinstance(
            through_sequence,
            int,
        )
        or through_sequence <= 0
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Chat receipt state "
                "is invalid."
            ),
        )

    if (
        isinstance(
            modified_count,
            bool,
        )
        or not isinstance(
            modified_count,
            int,
        )
        or modified_count < 0
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Chat receipt state "
                "is invalid."
            ),
        )

    receipt_payload = {
        "conversation_id": (
            conversation_id
        ),
        "through_sequence": (
            through_sequence
        ),
        "modified_count": (
            modified_count
        ),
        "user_id": str(
            identity[
                "user_id"
            ]
        ),
    }

    # Repeating an already-applied receipt is valid and
    # receives an ACK, but must not create duplicate
    # realtime receipt notifications.
    if modified_count > 0:
        socketio.emit(
            event_name,
            receipt_payload,
            to=conversation_room_name(
                conversation_id
            ),
            namespace="/",
        )

        # Reading messages changes this user's unread/chat-list
        # state. Synchronize that personalized conversation state
        # to every connected device owned by the reader, even if
        # those sockets never joined the conversation room.
        if event_name == MESSAGE_READ_EVENT:
            conversation = result.get(
                "conversation"
            )

            if isinstance(
                conversation,
                dict,
            ):
                try:
                    emit_conversation_update(
                        user_id=identity[
                            "user_id"
                        ],
                        conversation=conversation,
                        message=None,
                        reason="message.read",
                    )

                except (
                    RealtimeNotificationRoutingError,
                    ValueError,
                ):
                    # Receipt persistence is authoritative.
                    # Supplementary realtime notification failure
                    # must never invalidate the durable read state.
                    pass

    return {
        "ok": True,
        **result,
    }


@socketio.on(
    MESSAGE_DELIVERED_EVENT
)
def handle_message_delivered(
    payload,
):
    return _handle_message_receipt(
        payload,
        service_function=(
            mark_chat_delivered
        ),
        event_name=(
            MESSAGE_DELIVERED_EVENT
        ),
    )


@socketio.on(
    MESSAGE_READ_EVENT
)
def handle_message_read(
    payload,
):
    return _handle_message_receipt(
        payload,
        service_function=(
            mark_chat_read
        ),
        event_name=(
            MESSAGE_READ_EVENT
        ),
    )


@socketio.on(
    TYPING_START_EVENT
)
def handle_typing_start(
    payload,
):
    """
    Mark this authenticated socket as actively typing
    in one authorized conversation.

    State is ephemeral and automatically expires in Redis.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        (
            identity,
            conversation,
        ) = _authorized_conversation(
            conversation_id
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    canonical_conversation_id = str(
        conversation[
            "id"
        ]
    )

    user_id = str(
        identity[
            "user_id"
        ]
    )

    try:
        ttl = (
            _typing_ttl_seconds()
        )

        key = typing_state_key(
            conversation_id=(
                canonical_conversation_id
            ),
            user_id=user_id,
            socket_id=request.sid,
        )

        redis = (
            redis_store.get_client()
        )

        redis.set(
            key,
            "1",
            ex=ttl,
        )

    except (
        RedisError,
        RuntimeError,
        ValueError,
    ):
        return _typing_redis_error()

    event_payload = {
        "conversation_id": (
            canonical_conversation_id
        ),
        "user_id": user_id,
        "typing": True,
        "expires_in": ttl,
    }

    socketio.emit(
        TYPING_START_EVENT,
        event_payload,
        to=conversation_room_name(
            canonical_conversation_id
        ),
        skip_sid=request.sid,
        namespace="/",
    )

    return {
        "ok": True,
        **event_payload,
    }


@socketio.on(
    TYPING_STOP_EVENT
)
def handle_typing_stop(
    payload,
):
    """
    Clear this socket's typing state.

    If another socket belonging to the same user is still
    typing in the conversation, no false typing.stop event
    is broadcast.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        (
            identity,
            conversation,
        ) = _authorized_conversation(
            conversation_id
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    canonical_conversation_id = str(
        conversation[
            "id"
        ]
    )

    user_id = str(
        identity[
            "user_id"
        ]
    )

    try:
        base = _typing_key_base(
            conversation_id=(
                canonical_conversation_id
            ),
            user_id=user_id,
        )

        key = typing_state_key(
            conversation_id=(
                canonical_conversation_id
            ),
            user_id=user_id,
            socket_id=request.sid,
        )

        redis = (
            redis_store.get_client()
        )

        deleted = int(
            redis.delete(
                key
            )
            or 0
        )

        remaining_key = next(
            redis.scan_iter(
                match=f"{base}:*",
                count=10,
            ),
            None,
        )

    except (
        RedisError,
        RuntimeError,
        ValueError,
    ):
        return _typing_redis_error()

    still_typing = (
        remaining_key
        is not None
    )

    event_payload = {
        "conversation_id": (
            canonical_conversation_id
        ),
        "user_id": user_id,
        "typing": False,
    }

    # Do not broadcast a duplicate stop, and do not announce
    # that the user stopped while another authenticated socket
    # still has an active typing marker.
    if (
        deleted > 0
        and not still_typing
    ):
        socketio.emit(
            TYPING_STOP_EVENT,
            event_payload,
            to=conversation_room_name(
                canonical_conversation_id
            ),
            skip_sid=request.sid,
            namespace="/",
        )

    return {
        "ok": True,
        **event_payload,
        "changed": (
            deleted > 0
        ),
        "still_typing": (
            still_typing
        ),
    }


def _emit_recipient_conversation_update(
    message,
):
    """
    Refresh the recipient's chat-list/unread state even when
    none of their sockets joined the conversation room.

    This is supplementary realtime delivery. Failure here must
    never roll back or invalidate the already-durable message.
    """

    if not isinstance(
        message,
        dict,
    ):
        return False

    conversation_id = str(
        message.get(
            "conversation_id"
        )
        or ""
    ).strip()

    recipient_user_id = str(
        message.get(
            "recipient_id"
        )
        or ""
    ).strip()

    if (
        not conversation_id
        or not recipient_user_id
    ):
        return False

    try:
        conversation = (
            get_my_conversation(
                user_id=(
                    recipient_user_id
                ),
                conversation_id=(
                    conversation_id
                ),
            )
        )

        emit_conversation_update(
            user_id=(
                recipient_user_id
            ),
            conversation=conversation,
            message=message,
            reason="message.new",
        )

    except (
        ChatServiceError,
        RealtimeNotificationRoutingError,
        ValueError,
    ):
        return False

    return True


def _presence_not_available():
    return _error_payload(
        code="PRESENCE_NOT_AVAILABLE",
        message=(
            "Presence is not available "
            "for this conversation."
        ),
    )


def _presence_subject_is_available(
    user_id,
):
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


@socketio.on(
    PRESENCE_GET_EVENT
)
def handle_presence_get(
    payload,
):
    """
    Return the current presence state of the other participant
    in one authorized active conversation.

    The client supplies only conversation_id. The observed user
    is always derived by the server from conversation membership.
    """

    (
        conversation_id,
        error,
    ) = _require_event_payload(
        payload
    )

    if error is not None:
        return error

    try:
        (
            identity,
            conversation,
        ) = _authorized_conversation(
            conversation_id
        )

    except RealtimeAuthError as exc:
        return _auth_error(
            exc
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    if not isinstance(
        conversation,
        dict,
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Conversation state "
                "is invalid."
            ),
        )

    canonical_conversation_id = str(
        conversation.get(
            "id"
        )
        or ""
    ).strip()

    other_user_id = str(
        conversation.get(
            "other_user_id"
        )
        or ""
    ).strip()

    observer_user_id = str(
        identity.get(
            "user_id"
        )
        or ""
    ).strip()

    if (
        not canonical_conversation_id
        or not other_user_id
        or not observer_user_id
    ):
        return _error_payload(
            code="CHAT_STATE_INVALID",
            message=(
                "Conversation state "
                "is invalid."
            ),
        )

    if (
        conversation.get(
            "status"
        )
        != "active"
    ):
        return _presence_not_available()

    try:
        authorized_observers = (
            list_presence_observer_ids(
                other_user_id
            )
        )

        if (
            observer_user_id
            not in {
                str(value).strip()
                for value
                in authorized_observers
            }
        ):
            return _presence_not_available()

        if not (
            _presence_subject_is_available(
                other_user_id
            )
        ):
            return _presence_not_available()

    except RealtimeNotificationRoutingError as exc:
        return _error_payload(
            code=exc.code,
            message=exc.message,
        )

    except Exception:
        return _error_payload(
            code=ROUTING_ERROR_CODE,
            message=ROUTING_ERROR_MESSAGE,
        )

    try:
        online = is_user_online(
            user_id=other_user_id
        )

    except PresenceStateError as exc:
        return _error_payload(
            code=exc.code,
            message=exc.message,
        )

    return {
        "ok": True,
        "presence": {
            "conversation_id": (
                canonical_conversation_id
            ),
            "user_id": (
                other_user_id
            ),
            "online": bool(
                online
            ),
        },
    }


__all__ = [
    "CHAT_ROOM_PREFIX",
    "MESSAGE_DELIVERED_EVENT",
    "MESSAGE_NEW_EVENT",
    "MESSAGE_READ_EVENT",
    "MESSAGE_SEND_EVENT",
    "TYPING_START_EVENT",
    "TYPING_STOP_EVENT",
    "conversation_room_name",
    "handle_chat_join",
    "handle_chat_leave",
    "handle_message_delivered",
    "handle_message_read",
    "handle_message_send",
    "handle_typing_start",
    "handle_typing_stop",
    "typing_state_key",
    "PRESENCE_GET_EVENT",
    "handle_presence_get",
]
