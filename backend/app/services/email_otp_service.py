"""Secure Redis-backed OTP service for Soultie email authentication."""

import hashlib
import hmac
import secrets

from email_validator import (
    EmailNotValidError,
    validate_email,
)
from flask import current_app

from app.extensions import redis_store
from app.services.otp_service import (
    InvalidOtp,
    OtpAttemptsExceeded,
    OtpCooldownActive,
    OtpError,
    OtpExpired,
    OtpRateLimited,
    _REQUEST_GATE_SCRIPT,
    _VERIFY_SCRIPT,
)


class InvalidEmailAddress(OtpError):
    """Raised when an email address is invalid."""


def normalize_email(
    email: str,
) -> str:
    raw = str(
        email or ""
    ).strip()

    if not raw:
        raise InvalidEmailAddress(
            "Enter a valid email address."
        )

    try:
        result = validate_email(
            raw,
            check_deliverability=False,
        )

    except EmailNotValidError as exc:
        raise InvalidEmailAddress(
            "Enter a valid email address."
        ) from exc

    normalized = str(
        result.normalized
    ).strip().casefold()

    if (
        not normalized
        or len(normalized) > 254
    ):
        raise InvalidEmailAddress(
            "Enter a valid email address."
        )

    return normalized


def _identifier_digest(
    email: str,
) -> str:
    return hashlib.sha256(
        email.encode(
            "utf-8"
        )
    ).hexdigest()


def _redis_key(
    purpose: str,
    email: str,
) -> str:
    prefix = current_app.config[
        "EMAIL_OTP_KEY_PREFIX"
    ]

    identifier = _identifier_digest(
        email
    )

    return (
        f"{prefix}:{purpose}:{identifier}"
    )


def _hash_otp(
    email: str,
    otp: str,
) -> str:
    secret = current_app.config[
        "OTP_HASH_SECRET"
    ]

    message = (
        f"email:{email}:{otp}"
    ).encode(
        "utf-8"
    )

    return hmac.new(
        str(secret).encode(
            "utf-8"
        ),
        message,
        hashlib.sha256,
    ).hexdigest()


def _generate_email_otp() -> str:
    configured_code = str(
        current_app.config.get(
            "DEV_EMAIL_OTP_CODE",
            "",
        )
    ).strip()

    if (
        current_app.config.get(
            "EXPOSE_DEV_OTP",
            False,
        )
        and configured_code
    ):
        return configured_code

    length = int(
        current_app.config[
            "OTP_LENGTH"
        ]
    )

    if (
        length < 4
        or length > 8
    ):
        raise RuntimeError(
            (
                "OTP_LENGTH must be "
                "between 4 and 8."
            )
        )

    upper_bound = (
        10 ** length
    )

    return str(
        secrets.randbelow(
            upper_bound
        )
    ).zfill(
        length
    )


def create_email_otp_challenge(
    email: str,
):
    normalized_email = (
        normalize_email(
            email
        )
    )

    redis = (
        redis_store.get_client()
    )

    cooldown_key = _redis_key(
        "cooldown",
        normalized_email,
    )

    rate_key = _redis_key(
        "requests",
        normalized_email,
    )

    result = redis.eval(
        _REQUEST_GATE_SCRIPT,
        2,
        cooldown_key,
        rate_key,
        int(
            current_app.config[
                "OTP_RESEND_COOLDOWN_SECONDS"
            ]
        ),
        int(
            current_app.config[
                "OTP_REQUEST_WINDOW_SECONDS"
            ]
        ),
        int(
            current_app.config[
                "OTP_MAX_REQUESTS_PER_WINDOW"
            ]
        ),
    )

    allowed = int(
        result[0]
    )

    status = str(
        result[1]
    )

    retry_after = int(
        result[2]
    )

    if not allowed:
        if (
            status
            == "cooldown"
        ):
            raise OtpCooldownActive(
                retry_after
            )

        if (
            status
            == "rate_limited"
        ):
            raise OtpRateLimited(
                retry_after
            )

        raise OtpError(
            (
                "Email OTP request "
                "could not be processed."
            )
        )

    otp = (
        _generate_email_otp()
    )

    code_key = _redis_key(
        "code",
        normalized_email,
    )

    attempts_key = _redis_key(
        "attempts",
        normalized_email,
    )

    redis.delete(
        attempts_key
    )

    redis.set(
        code_key,
        _hash_otp(
            normalized_email,
            otp,
        ),
        ex=int(
            current_app.config[
                "OTP_TTL_SECONDS"
            ]
        ),
    )

    response = {
        "email": normalized_email,
        "expires_in": int(
            current_app.config[
                "OTP_TTL_SECONDS"
            ]
        ),
        "resend_after": int(
            current_app.config[
                "OTP_RESEND_COOLDOWN_SECONDS"
            ]
        ),
    }

    if current_app.config.get(
        "EXPOSE_DEV_OTP",
        False,
    ):
        response[
            "dev_otp"
        ] = otp

    return response, otp


def request_email_otp(
    email: str,
) -> dict:
    response, _ = (
        create_email_otp_challenge(
            email
        )
    )

    return response


def invalidate_email_otp(
    email: str,
    *,
    clear_cooldown=False,
):
    normalized_email = (
        normalize_email(
            email
        )
    )

    redis = (
        redis_store.get_client()
    )

    keys = [
        _redis_key(
            "code",
            normalized_email,
        ),
        _redis_key(
            "attempts",
            normalized_email,
        ),
    ]

    if clear_cooldown:
        keys.append(
            _redis_key(
                "cooldown",
                normalized_email,
            )
        )

    redis.delete(
        *keys
    )


def verify_email_otp(
    email: str,
    otp: str,
) -> dict:
    normalized_email = (
        normalize_email(
            email
        )
    )

    supplied_otp = str(
        otp or ""
    ).strip()

    if not supplied_otp.isdigit():
        raise InvalidOtp(
            "OTP is invalid."
        )

    redis = (
        redis_store.get_client()
    )

    code_key = _redis_key(
        "code",
        normalized_email,
    )

    attempts_key = _redis_key(
        "attempts",
        normalized_email,
    )

    result = redis.eval(
        _VERIFY_SCRIPT,
        2,
        code_key,
        attempts_key,
        _hash_otp(
            normalized_email,
            supplied_otp,
        ),
        int(
            current_app.config[
                "OTP_MAX_ATTEMPTS"
            ]
        ),
    )

    verified = int(
        result[0]
    )

    status = str(
        result[1]
    )

    remaining_attempts = int(
        result[2]
    )

    if verified:
        return {
            "verified": True,
            "email": normalized_email,
        }

    if (
        status
        == "expired"
    ):
        raise OtpExpired(
            (
                "OTP has expired or "
                "was already used."
            )
        )

    if (
        status
        == "attempts_exceeded"
    ):
        raise OtpAttemptsExceeded(
            (
                "Too many incorrect "
                "OTP attempts."
            )
        )

    if (
        status
        == "invalid"
    ):
        error = InvalidOtp(
            "OTP is incorrect."
        )

        error.remaining_attempts = (
            remaining_attempts
        )

        raise error

    raise OtpError(
        (
            "Email OTP verification "
            "could not be processed."
        )
    )
