from datetime import datetime, timedelta, timezone

from flask import current_app
from pymongo.errors import DuplicateKeyError

from app.repositories.auth_repository import (
    create_identity,
    get_identity,
    get_identity_by_email,
    get_identity_by_phone,
)
from app.repositories.session_repository import (
    claim_session_for_rotation,
    create_session,
    get_session_by_refresh_hash,
    revoke_all_user_sessions,
    revoke_family,
    revoke_session_by_refresh_hash,
)
from app.repositories.user_repository import (
    create_user,
    delete_user,
    get_user_by_id,
    update_last_login,
)
from app.security.tokens import (
    create_access_token,
    generate_refresh_token,
    generate_session_id,
    generate_token_family_id,
    hash_refresh_token,
)
from app.services.email_otp_service import (
    verify_email_otp,
)
from app.services.google_token_service import (
    verify_google_id_token,
)
from app.services.otp_service import verify_otp


class AuthenticationError(Exception):
    """Base authentication service error."""


class InvalidRefreshToken(AuthenticationError):
    """Raised when a refresh token cannot be used."""


class RefreshTokenReuseDetected(AuthenticationError):
    """Raised when a rotated refresh token is reused."""


class AccountUnavailable(AuthenticationError):
    """Raised when an account cannot authenticate."""


class GoogleAccountConflict(AuthenticationError):
    """Raised when Google identity ownership conflicts."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _refresh_expiry() -> datetime:
    """Long-lived rolling mobile session.

    Ordinary access-token expiry does not end the login.
    Refresh rotation extends the session lifetime.
    Logout, revocation and replay protections remain enforced.
    """
    days = current_app.config.get(
        "PERSISTENT_LOGIN_REFRESH_DAYS", 3650
    )
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or not 1 <= days <= 3650
    ):
        raise ValueError(
            "PERSISTENT_LOGIN_REFRESH_DAYS must be 1..3650."
        )
    return utc_now() + timedelta(days=days)



def _build_token_response(
    *,
    user_id,
    session_id,
    refresh_token,
):
    access_token = create_access_token(
        str(user_id),
        session_id=session_id,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": (
            current_app.config[
                "JWT_ACCESS_TOKEN_MINUTES"
            ]
            * 60
        ),
        "session_id": session_id,
    }


def issue_session(
    user_id,
    *,
    metadata=None,
):
    session_id = generate_session_id()
    family_id = generate_token_family_id()

    refresh_token = generate_refresh_token()

    create_session(
        user_id=user_id,
        session_id=session_id,
        family_id=family_id,
        refresh_token_hash=hash_refresh_token(
            refresh_token
        ),
        expires_at=_refresh_expiry(),
        metadata=metadata,
    )

    return _build_token_response(
        user_id=user_id,
        session_id=session_id,
        refresh_token=refresh_token,
    )


def rotate_refresh_token(
    refresh_token: str,
    *,
    metadata=None,
):
    if not refresh_token:
        raise InvalidRefreshToken(
            "Refresh token is required."
        )

    token_hash = hash_refresh_token(
        refresh_token
    )

    existing = get_session_by_refresh_hash(
        token_hash
    )

    if existing is None:
        raise InvalidRefreshToken(
            "Refresh token is invalid."
        )

    if existing.get(
        "revoked_at"
    ) is not None:
        if (
            existing.get(
                "revoke_reason"
            )
            == "rotated"
            or existing.get(
                "replaced_by_session_id"
            )
        ):
            revoke_family(
                existing["family_id"],
                reason="refresh_token_reuse",
            )

            raise RefreshTokenReuseDetected(
                "Refresh token reuse detected."
            )

        raise InvalidRefreshToken(
            "Refresh token has been revoked."
        )

    new_session_id = generate_session_id()

    previous_session = (
        claim_session_for_rotation(
            refresh_token_hash=token_hash,
            replacement_session_id=(
                new_session_id
            ),
        )
    )

    if previous_session is None:
        latest = get_session_by_refresh_hash(
            token_hash
        )

        if latest and (
            latest.get(
                "revoke_reason"
            )
            == "rotated"
            or latest.get(
                "replaced_by_session_id"
            )
        ):
            revoke_family(
                latest["family_id"],
                reason="refresh_token_reuse",
            )

            raise RefreshTokenReuseDetected(
                "Refresh token reuse detected."
            )

        raise InvalidRefreshToken(
            "Refresh token is expired or unavailable."
        )

    new_refresh_token = (
        generate_refresh_token()
    )

    create_session(
        user_id=previous_session[
            "user_id"
        ],
        session_id=new_session_id,
        family_id=previous_session[
            "family_id"
        ],
        refresh_token_hash=(
            hash_refresh_token(
                new_refresh_token
            )
        ),
        expires_at=_refresh_expiry(),
        parent_session_id=(
            previous_session[
                "session_id"
            ]
        ),
        metadata=metadata,
    )

    return _build_token_response(
        user_id=previous_session[
            "user_id"
        ],
        session_id=new_session_id,
        refresh_token=new_refresh_token,
    )


def logout_refresh_token(
    refresh_token: str,
):
    if not refresh_token:
        return False

    return revoke_session_by_refresh_hash(
        hash_refresh_token(
            refresh_token
        ),
        reason="logout",
    )


def logout_all_sessions(
    user_id,
):
    return revoke_all_user_sessions(
        user_id,
        reason="logout_all",
    )


def authenticate_phone_otp(
    phone: str,
    otp: str,
    *,
    metadata=None,
):
    verification = verify_otp(
        phone,
        otp,
    )

    normalized_phone = verification[
        "phone"
    ]

    identity = get_identity_by_phone(
        normalized_phone
    )

    is_new_user = False

    if identity is not None:
        user = get_user_by_id(
            identity["user_id"]
        )

    else:
        user = create_user()

        try:
            identity = create_identity(
                user_id=user["_id"],
                provider="phone",
                provider_subject=(
                    normalized_phone
                ),
                phone_normalized=(
                    normalized_phone
                ),
                verified=True,
            )

            is_new_user = True

        except DuplicateKeyError:
            # Another request created the identity first.
            # Roll back the unused user and load the winner.
            delete_user(
                user["_id"]
            )

            identity = get_identity_by_phone(
                normalized_phone
            )

            if identity is None:
                raise AuthenticationError(
                    "Authentication identity could not be created."
                )

            user = get_user_by_id(
                identity["user_id"]
            )

    if user is None:
        raise AuthenticationError(
            "Authentication account is unavailable."
        )

    account_status = user.get(
        "account_status",
        "active",
    )

    if account_status in {
        "suspended",
        "banned",
        "deleted",
    }:
        raise AccountUnavailable(
            "This account is unavailable."
        )

    update_last_login(
        user["_id"]
    )

    tokens = issue_session(
        user["_id"],
        metadata=metadata,
    )

    return {
        "user": user,
        "phone": normalized_phone,
        "is_new_user": is_new_user,
        "tokens": tokens,
    }




def authenticate_email_otp(
    email: str,
    otp: str,
    *,
    metadata=None,
):
    verification = verify_email_otp(
        email,
        otp,
    )

    normalized_email = verification[
        "email"
    ]

    identity = get_identity_by_email(
        normalized_email
    )

    is_new_user = False

    if identity is not None:
        user = get_user_by_id(
            identity["user_id"]
        )

    else:
        user = create_user()

        try:
            identity = create_identity(
                user_id=user["_id"],
                provider="email",
                provider_subject=(
                    normalized_email
                ),
                email_normalized=(
                    normalized_email
                ),
                verified=True,
            )

            is_new_user = True

        except DuplicateKeyError:
            # Another request created the
            # email identity first.
            delete_user(
                user["_id"]
            )

            identity = (
                get_identity_by_email(
                    normalized_email
                )
            )

            if identity is None:
                raise AuthenticationError(
                    (
                        "Authentication identity "
                        "could not be created."
                    )
                )

            user = get_user_by_id(
                identity["user_id"]
            )

    if user is None:
        raise AuthenticationError(
            (
                "Authentication account "
                "is unavailable."
            )
        )

    account_status = user.get(
        "account_status",
        "active",
    )

    if account_status in {
        "suspended",
        "banned",
        "deleted",
    }:
        raise AccountUnavailable(
            "This account is unavailable."
        )

    update_last_login(
        user["_id"]
    )

    tokens = issue_session(
        user["_id"],
        metadata=metadata,
    )

    return {
        "user": user,
        "email": normalized_email,
        "is_new_user": is_new_user,
        "tokens": tokens,
    }





def _google_identity_metadata(
    claims: dict,
):
    metadata = {
        "email": claims["email"],
        "email_verified": True,
    }

    for key in (
        "name",
        "picture",
        "hosted_domain",
    ):
        value = claims.get(
            key
        )

        if value:
            metadata[key] = value

    return metadata


def _link_google_identity(
    *,
    user_id,
    subject,
    claims,
):
    try:
        return create_identity(
            user_id=user_id,
            provider="google",
            provider_subject=subject,
            verified=True,
            metadata=(
                _google_identity_metadata(
                    claims
                )
            ),
        )

    except DuplicateKeyError:
        identity = get_identity(
            provider="google",
            provider_subject=subject,
        )

        if identity is None:
            raise AuthenticationError(
                (
                    "Google identity could "
                    "not be linked."
                )
            )

        if (
            str(identity["user_id"])
            != str(user_id)
        ):
            raise GoogleAccountConflict(
                (
                    "This Google account is "
                    "already linked to another "
                    "Xuoroni account."
                )
            )

        return identity


def authenticate_google_id_token(
    id_token: str,
    *,
    metadata=None,
):
    claims = verify_google_id_token(
        id_token
    )

    subject = claims[
        "sub"
    ]

    email = claims[
        "email"
    ]

    google_identity = get_identity(
        provider="google",
        provider_subject=subject,
    )

    email_identity = (
        get_identity_by_email(
            email
        )
    )

    is_new_user = False
    linked_existing_account = False

    if google_identity is not None:
        if (
            email_identity is not None
            and str(
                email_identity[
                    "user_id"
                ]
            )
            != str(
                google_identity[
                    "user_id"
                ]
            )
        ):
            raise GoogleAccountConflict(
                (
                    "The verified Google email "
                    "belongs to a different "
                    "Xuoroni account."
                )
            )

        user = get_user_by_id(
            google_identity[
                "user_id"
            ]
        )

    elif email_identity is not None:
        user = get_user_by_id(
            email_identity[
                "user_id"
            ]
        )

        if user is None:
            raise AuthenticationError(
                (
                    "Authentication account "
                    "is unavailable."
                )
            )

        _link_google_identity(
            user_id=user["_id"],
            subject=subject,
            claims=claims,
        )

        linked_existing_account = True

    else:
        user = create_user()

        try:
            create_identity(
                user_id=user["_id"],
                provider="google",
                provider_subject=subject,
                email_normalized=email,
                verified=True,
                metadata=(
                    _google_identity_metadata(
                        claims
                    )
                ),
            )

            is_new_user = True

        except DuplicateKeyError:
            # A concurrent login may have
            # created the Google subject or
            # claimed the verified email.
            delete_user(
                user["_id"]
            )

            google_identity = (
                get_identity(
                    provider="google",
                    provider_subject=subject,
                )
            )

            email_identity = (
                get_identity_by_email(
                    email
                )
            )

            if (
                google_identity is not None
                and email_identity is not None
                and str(
                    google_identity[
                        "user_id"
                    ]
                )
                != str(
                    email_identity[
                        "user_id"
                    ]
                )
            ):
                raise GoogleAccountConflict(
                    (
                        "Google identity and "
                        "verified email belong "
                        "to different accounts."
                    )
                )

            if google_identity is not None:
                user = get_user_by_id(
                    google_identity[
                        "user_id"
                    ]
                )

            elif email_identity is not None:
                user = get_user_by_id(
                    email_identity[
                        "user_id"
                    ]
                )

                if user is None:
                    raise AuthenticationError(
                        (
                            "Authentication "
                            "account is "
                            "unavailable."
                        )
                    )

                _link_google_identity(
                    user_id=user["_id"],
                    subject=subject,
                    claims=claims,
                )

                linked_existing_account = True

            else:
                raise AuthenticationError(
                    (
                        "Google authentication "
                        "could not be completed."
                    )
                )

    if user is None:
        raise AuthenticationError(
            (
                "Authentication account "
                "is unavailable."
            )
        )

    account_status = user.get(
        "account_status",
        "active",
    )

    if account_status in {
        "suspended",
        "banned",
        "deleted",
    }:
        raise AccountUnavailable(
            "This account is unavailable."
        )

    update_last_login(
        user["_id"]
    )

    tokens = issue_session(
        user["_id"],
        metadata=metadata,
    )

    return {
        "user": user,
        "email": email,
        "is_new_user": is_new_user,
        "linked_existing_account": (
            linked_existing_account
        ),
        "tokens": tokens,
    }
