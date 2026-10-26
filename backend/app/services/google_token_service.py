"""Secure Google ID-token verification for Soultie."""

from flask import current_app

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token

from app.services.email_otp_service import (
    InvalidEmailAddress,
    normalize_email,
)


class GoogleTokenError(Exception):
    """Base Google authentication token error."""


class GoogleConfigurationError(
    GoogleTokenError
):
    """Raised when Google authentication is not configured."""


class InvalidGoogleToken(
    GoogleTokenError
):
    """Raised when a Google ID token is invalid."""


class GoogleEmailNotVerified(
    GoogleTokenError
):
    """Raised when Google has not verified the email."""


def verify_google_id_token(
    token: str,
) -> dict:
    token = str(
        token or ""
    ).strip()

    if not token:
        raise InvalidGoogleToken(
            "Google ID token is required."
        )

    client_id = str(
        current_app.config.get(
            "GOOGLE_CLIENT_ID",
            "",
        )
        or ""
    ).strip()

    if not client_id:
        raise GoogleConfigurationError(
            (
                "Google authentication "
                "is not configured."
            )
        )

    try:
        claims = (
            google_id_token.verify_oauth2_token(
                token,
                Request(),
                audience=client_id,
            )
        )

    except (
        ValueError,
        GoogleAuthError,
    ) as exc:
        raise InvalidGoogleToken(
            "Google ID token is invalid."
        ) from exc

    subject = str(
        claims.get(
            "sub",
            "",
        )
        or ""
    ).strip()

    if not subject:
        raise InvalidGoogleToken(
            (
                "Google ID token does not "
                "contain a valid subject."
            )
        )

    if (
        claims.get(
            "email_verified"
        )
        is not True
    ):
        raise GoogleEmailNotVerified(
            (
                "Google email address "
                "is not verified."
            )
        )

    raw_email = claims.get(
        "email"
    )

    try:
        normalized_email = (
            normalize_email(
                raw_email
            )
        )

    except InvalidEmailAddress as exc:
        raise InvalidGoogleToken(
            (
                "Google ID token contains "
                "an invalid email address."
            )
        ) from exc

    result = {
        "sub": subject,
        "email": normalized_email,
        "email_verified": True,
    }

    name = str(
        claims.get(
            "name",
            "",
        )
        or ""
    ).strip()

    picture = str(
        claims.get(
            "picture",
            "",
        )
        or ""
    ).strip()

    hosted_domain = str(
        claims.get(
            "hd",
            "",
        )
        or ""
    ).strip()

    if name:
        result["name"] = name[:200]

    if picture:
        result["picture"] = picture[:1000]

    if hosted_domain:
        result["hosted_domain"] = (
            hosted_domain[:255]
        )

    return result
