"""Authentication helpers for Xuoroni Socket.IO realtime connections."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId

from app.repositories.session_repository import (
    get_session_by_id,
)
from app.repositories.user_repository import (
    get_user_by_id,
)
from app.security.tokens import (
    InvalidAccessToken,
    decode_access_token,
)


class RealtimeAuthError(
    RuntimeError
):
    """Authentication failure for realtime connections."""

    def __init__(
        self,
        code,
        message,
    ):
        super().__init__(
            message
        )

        self.code = code
        self.message = message


def utc_now():
    return datetime.now(
        timezone.utc
    )


def _as_utc(
    value,
):
    if value is None:
        return None

    if not isinstance(
        value,
        datetime,
    ):
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _to_object_id(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(value)
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        return None


def _extract_access_token(
    auth,
):
    if not isinstance(
        auth,
        dict,
    ):
        raise RealtimeAuthError(
            "REALTIME_AUTHENTICATION_REQUIRED",
            (
                "A valid access token "
                "is required."
            ),
        )

    token = auth.get(
        "access_token"
    )

    if token is None:
        token = auth.get(
            "authorization"
        )

    if token is None:
        token = auth.get(
            "Authorization"
        )

    if not isinstance(
        token,
        str,
    ):
        raise RealtimeAuthError(
            "REALTIME_AUTHENTICATION_REQUIRED",
            (
                "A valid access token "
                "is required."
            ),
        )

    token = token.strip()

    if token.lower() == "bearer":
        token = ""

    elif token.lower().startswith(
        "bearer "
    ):
        token = token[
            7:
        ].strip()

    if not token:
        raise RealtimeAuthError(
            "REALTIME_AUTHENTICATION_REQUIRED",
            (
                "A valid access token "
                "is required."
            ),
        )

    return token


def validate_realtime_identity(
    *,
    user_id,
    session_id,
):
    """
    Revalidate an authenticated realtime identity.

    Protected Socket.IO events should call this so logout,
    revocation, session expiry and account suspension take
    effect even while a socket remains connected.
    """

    user_object_id = (
        _to_object_id(
            user_id
        )
    )

    if user_object_id is None:
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    if (
        not isinstance(
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

    normalized_session_id = (
        session_id.strip()
    )

    session = get_session_by_id(
        normalized_session_id
    )

    if session is None:
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    if (
        session.get(
            "revoked_at"
        )
        is not None
    ):
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    session_expires_at = (
        _as_utc(
            session.get(
                "expires_at"
            )
        )
    )

    if (
        session_expires_at is None
        or session_expires_at
        <= utc_now()
    ):
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    session_user_id = (
        _to_object_id(
            session.get(
                "user_id"
            )
        )
    )

    if (
        session_user_id is None
        or session_user_id
        != user_object_id
    ):
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The realtime session "
                "is no longer valid."
            ),
        )

    user = get_user_by_id(
        user_object_id
    )

    if (
        user is None
        or user.get(
            "account_status"
        )
        != "active"
    ):
        raise RealtimeAuthError(
            "ACCOUNT_UNAVAILABLE",
            (
                "This account is "
                "unavailable."
            ),
        )

    return {
        "user": user,
        "user_id": (
            user_object_id
        ),
        "session": session,
        "session_id": (
            normalized_session_id
        ),
    }


def authenticate_realtime_connection(
    auth,
):
    """
    Authenticate a Socket.IO handshake.

    The client must provide the normal Xuoroni access token
    through the Socket.IO auth payload. The access token is
    accepted only while its backing auth session remains active.
    """

    access_token = (
        _extract_access_token(
            auth
        )
    )

    try:
        payload = (
            decode_access_token(
                access_token
            )
        )

    except InvalidAccessToken:
        raise RealtimeAuthError(
            "INVALID_ACCESS_TOKEN",
            (
                "The access token is "
                "invalid or expired."
            ),
        ) from None

    identity = (
        validate_realtime_identity(
            user_id=payload.get(
                "sub"
            ),
            session_id=payload.get(
                "sid"
            ),
        )
    )

    return {
        **identity,
        "token_payload": payload,
    }


__all__ = [
    "RealtimeAuthError",
    "authenticate_realtime_connection",
    "validate_realtime_identity",
]
