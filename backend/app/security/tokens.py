import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from flask import current_app


class TokenError(Exception):
    """Base Soultie token error."""


class InvalidAccessToken(TokenError):
    """Raised when an access token is invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: str,
    *,
    session_id: str,
) -> str:
    now = _utc_now()

    expires_at = now + timedelta(
        minutes=current_app.config[
            "JWT_ACCESS_TOKEN_MINUTES"
        ]
    )

    payload = {
        "iss": current_app.config[
            "JWT_ISSUER"
        ],
        "aud": current_app.config[
            "JWT_AUDIENCE"
        ],
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        current_app.config[
            "JWT_SECRET_KEY"
        ],
        algorithm=current_app.config[
            "JWT_ALGORITHM"
        ],
    )


def decode_access_token(
    token: str,
) -> dict:
    try:
        payload = jwt.decode(
            token,
            current_app.config[
                "JWT_SECRET_KEY"
            ],
            algorithms=[
                current_app.config[
                    "JWT_ALGORITHM"
                ]
            ],
            audience=current_app.config[
                "JWT_AUDIENCE"
            ],
            issuer=current_app.config[
                "JWT_ISSUER"
            ],
            options={
                "require": [
                    "exp",
                    "iat",
                    "iss",
                    "aud",
                    "sub",
                    "sid",
                    "type",
                    "jti",
                ]
            },
        )

    except jwt.PyJWTError as exc:
        raise InvalidAccessToken(
            "Access token is invalid or expired."
        ) from exc

    if payload.get("type") != "access":
        raise InvalidAccessToken(
            "Token type is invalid."
        )

    return payload


def generate_refresh_token() -> str:
    """
    Generate an opaque refresh token.

    The raw token is returned to the client.
    Only its SHA-256 hash should be stored in MongoDB.
    """
    return secrets.token_urlsafe(64)


def hash_refresh_token(
    token: str,
) -> str:
    if not token:
        raise ValueError(
            "Refresh token cannot be empty."
        )

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def generate_session_id() -> str:
    return uuid.uuid4().hex


def generate_token_family_id() -> str:
    """
    Identifies a rotating refresh-token family.

    If refresh-token reuse is detected later, every session
    in this family can be revoked.
    """
    return uuid.uuid4().hex
