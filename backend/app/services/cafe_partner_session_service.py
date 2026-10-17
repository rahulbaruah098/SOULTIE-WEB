"""Isolated opaque sessions for Xuoroni cafe partners.

These credentials must never authenticate dating-user or admin routes.
Only session token digests are persisted.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from bson import ObjectId

from app.extensions import mongo
from app.security.cafe_partner_access import (
    CafePartnerAccessDenied,
    authorize_cafe_partner,
)


CAFE_SESSION_HOURS = 12


class CafeSessionInvalid(PermissionError):
    """Cafe session missing, expired, revoked, or unauthorized."""


def _now():
    return datetime.now(timezone.utc)


def _digest(token):
    if (
        not isinstance(token, str)
        or not 40 <= len(token) <= 256
        or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_cafe_session(*, partner_id):
    """Issue a caf?-only bearer credential after live authorization.

    Call this only after a successful password-authentication step.
    The returned token is shown once and never stored in plaintext.
    """
    context = authorize_cafe_partner(partner_id=partner_id)
    issued_at = _now()
    expires_at = issued_at + timedelta(hours=CAFE_SESSION_HOURS)
    token = secrets.token_urlsafe(48)
    token_hash = _digest(token)

    session = {
        "partner_id": ObjectId(context["partner_id"]),
        "venue_id": ObjectId(context["venue_id"]),
        "token_hash": token_hash,
        "created_at": issued_at,
        "expires_at": expires_at,
        "revoked_at": None,
        "session_type": "cafe_partner",
    }

    mongo.db.cafe_partner_sessions.insert_one(session)

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": CAFE_SESSION_HOURS * 3600,
        "account_type": "cafe_partner",
        "partner_id": context["partner_id"],
        "venue_id": context["venue_id"],
    }


def authenticate_cafe_session(*, token):
    """Validate live session, account and venue on every request."""
    token_hash = _digest(token)
    session = mongo.db.cafe_partner_sessions.find_one({
        "token_hash": token_hash,
        "session_type": "cafe_partner",
        "revoked_at": None,
        "expires_at": {"$gt": _now()},
    })

    if not isinstance(session, dict):
        raise CafeSessionInvalid("Invalid cafe session.")

    partner_id = session.get("partner_id")
    venue_id = session.get("venue_id")

    if (
        not isinstance(partner_id, ObjectId)
        or not isinstance(venue_id, ObjectId)
    ):
        raise CafeSessionInvalid("Invalid cafe session.")

    try:
        return authorize_cafe_partner(
            partner_id=str(partner_id),
            venue_id=str(venue_id),
        )
    except CafePartnerAccessDenied:
        raise CafeSessionInvalid("Invalid cafe session.") from None


def revoke_cafe_session(*, token):
    """Revoke only the presented caf? token, never user or admin tokens."""
    token_hash = _digest(token)
    result = mongo.db.cafe_partner_sessions.update_one(
        {
            "token_hash": token_hash,
            "session_type": "cafe_partner",
            "revoked_at": None,
        },
        {"$set": {"revoked_at": _now()}},
    )
    return result.modified_count == 1

def revoke_all_cafe_sessions(*, partner_id):
    """Revoke every unrevoked cafe session for one partner.

    Caller must authenticate and authorize the requested action.
    This function never touches dating-user or admin sessions.
    """
    from bson.errors import InvalidId

    if not isinstance(partner_id, str):
        raise CafeSessionInvalid("Invalid cafe partner.")

    try:
        partner_object_id = ObjectId(partner_id)
    except (InvalidId, TypeError):
        raise CafeSessionInvalid("Invalid cafe partner.") from None

    result = mongo.db.cafe_partner_sessions.update_many(
        {
            "partner_id": partner_object_id,
            "session_type": "cafe_partner",
            "revoked_at": None,
        },
        {
            "$set": {
                "revoked_at": _now(),
            }
        },
    )
    return result.modified_count
