"""Redis-backed realtime presence state for Soultie."""

from flask import current_app
from redis.exceptions import RedisError

from app.extensions import redis_store


PRESENCE_ERROR_CODE = (
    "REALTIME_STATE_UNAVAILABLE"
)

PRESENCE_ERROR_MESSAGE = (
    "Realtime presence state "
    "is temporarily unavailable."
)


class PresenceStateError(
    RuntimeError
):
    """Raised when realtime presence state cannot be used."""

    def __init__(
        self,
        message=PRESENCE_ERROR_MESSAGE,
    ):
        self.code = (
            PRESENCE_ERROR_CODE
        )

        self.message = str(
            message
            or PRESENCE_ERROR_MESSAGE
        )

        super().__init__(
            self.message
        )


def _normalize_identifier(
    value,
    *,
    label,
):
    normalized = str(
        value
        or ""
    ).strip()

    if not normalized:
        raise ValueError(
            f"{label} cannot be empty."
        )

    return normalized


def _presence_prefix():
    prefix = str(
        current_app.config.get(
            "REALTIME_PRESENCE_KEY_PREFIX",
            "xuoroni:realtime:presence",
        )
        or ""
    ).strip()

    if not prefix:
        raise RuntimeError(
            (
                "Realtime presence key "
                "prefix is not configured."
            )
        )

    return prefix


def _presence_ttl_seconds():
    try:
        ttl = int(
            current_app.config.get(
                "REALTIME_PRESENCE_TTL_SECONDS",
                75,
            )
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            (
                "Realtime presence TTL "
                "is invalid."
            )
        ) from exc

    if ttl <= 0:
        raise RuntimeError(
            (
                "Realtime presence TTL "
                "must be positive."
            )
        )

    return ttl


def _presence_heartbeat_seconds():
    try:
        heartbeat = int(
            current_app.config.get(
                "REALTIME_PRESENCE_HEARTBEAT_SECONDS",
                25,
            )
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise RuntimeError(
            (
                "Realtime presence heartbeat "
                "is invalid."
            )
        ) from exc

    ttl = (
        _presence_ttl_seconds()
    )

    if heartbeat <= 0:
        raise RuntimeError(
            (
                "Realtime presence heartbeat "
                "must be positive."
            )
        )

    if heartbeat >= ttl:
        raise RuntimeError(
            (
                "Realtime presence heartbeat "
                "must be shorter than its TTL."
            )
        )

    return heartbeat


def presence_state_key(
    *,
    user_id,
    socket_id,
):
    user_value = (
        _normalize_identifier(
            user_id,
            label="User ID",
        )
    )

    socket_value = (
        _normalize_identifier(
            socket_id,
            label="Socket ID",
        )
    )

    return (
        f"{_presence_prefix()}:"
        f"{user_value}:"
        f"{socket_value}"
    )


def presence_user_pattern(
    *,
    user_id,
):
    user_value = (
        _normalize_identifier(
            user_id,
            label="User ID",
        )
    )

    return (
        f"{_presence_prefix()}:"
        f"{user_value}:*"
    )


def _redis_client():
    try:
        return (
            redis_store.get_client()
        )

    except RuntimeError as exc:
        raise PresenceStateError() from exc


def _count_user_sockets(
    redis,
    *,
    user_id,
):
    pattern = (
        presence_user_pattern(
            user_id=user_id
        )
    )

    try:
        return sum(
            1
            for _key in redis.scan_iter(
                match=pattern,
                count=20,
            )
        )

    except RedisError as exc:
        raise PresenceStateError() from exc


def count_user_presence_sockets(
    *,
    user_id,
):
    redis = (
        _redis_client()
    )

    return _count_user_sockets(
        redis,
        user_id=user_id,
    )


def is_user_online(
    *,
    user_id,
):
    return (
        count_user_presence_sockets(
            user_id=user_id
        )
        > 0
    )


def mark_socket_online(
    *,
    user_id,
    socket_id,
):
    """
    Create or refresh one authenticated socket's presence key.

    changed=True means the user transitioned from having no
    active presence keys to being online.
    """

    user_value = (
        _normalize_identifier(
            user_id,
            label="User ID",
        )
    )

    socket_value = (
        _normalize_identifier(
            socket_id,
            label="Socket ID",
        )
    )

    try:
        ttl = (
            _presence_ttl_seconds()
        )

        heartbeat = (
            _presence_heartbeat_seconds()
        )

        redis = (
            _redis_client()
        )

        before_count = (
            _count_user_sockets(
                redis,
                user_id=user_value,
            )
        )

        key = presence_state_key(
            user_id=user_value,
            socket_id=socket_value,
        )

        redis.set(
            key,
            "1",
            ex=ttl,
        )

        after_count = (
            _count_user_sockets(
                redis,
                user_id=user_value,
            )
        )

    except (
        RedisError,
        RuntimeError,
    ) as exc:
        if isinstance(
            exc,
            PresenceStateError,
        ):
            raise

        raise PresenceStateError() from exc

    return {
        "user_id": user_value,
        "online": True,
        "changed": (
            before_count == 0
            and after_count > 0
        ),
        "socket_count": (
            after_count
        ),
        "expires_in": ttl,
        "heartbeat_after": (
            heartbeat
        ),
    }


def refresh_socket_presence(
    *,
    user_id,
    socket_id,
):
    """
    Refresh this socket's TTL.

    If its stale key already expired and the user has no other
    sockets, this correctly becomes a new online transition.
    """

    return mark_socket_online(
        user_id=user_id,
        socket_id=socket_id,
    )


def remove_socket_presence(
    *,
    user_id,
    socket_id,
):
    """
    Remove one socket's presence marker.

    changed=True only when removing this socket causes the
    user's final active presence key to disappear.
    """

    user_value = (
        _normalize_identifier(
            user_id,
            label="User ID",
        )
    )

    socket_value = (
        _normalize_identifier(
            socket_id,
            label="Socket ID",
        )
    )

    try:
        redis = (
            _redis_client()
        )

        key = presence_state_key(
            user_id=user_value,
            socket_id=socket_value,
        )

        deleted = int(
            redis.delete(
                key
            )
            or 0
        )

        remaining_count = (
            _count_user_sockets(
                redis,
                user_id=user_value,
            )
        )

    except (
        RedisError,
        RuntimeError,
    ) as exc:
        if isinstance(
            exc,
            PresenceStateError,
        ):
            raise

        raise PresenceStateError() from exc

    online = (
        remaining_count > 0
    )

    return {
        "user_id": user_value,
        "online": online,
        "changed": (
            deleted > 0
            and not online
        ),
        "removed": (
            deleted > 0
        ),
        "socket_count": (
            remaining_count
        ),
    }


__all__ = [
    "PRESENCE_ERROR_CODE",
    "PRESENCE_ERROR_MESSAGE",
    "PresenceStateError",
    "count_user_presence_sockets",
    "is_user_online",
    "mark_socket_online",
    "presence_state_key",
    "presence_user_pattern",
    "refresh_socket_presence",
    "remove_socket_presence",
]
