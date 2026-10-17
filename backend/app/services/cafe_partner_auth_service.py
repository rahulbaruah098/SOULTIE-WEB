"""Caf? partner account foundation for Xuoroni.

Caf? identities are separate from dating-user and legacy admin identities.
This module never creates JWTs, authorizes Superadmins, or exposes routes.
Session issuance and account verification are separate implementation steps.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import mongo
from app.schemas.venue import validate_cafe_registration


CAFE_ACCOUNT_PENDING = "pending"
CAFE_ACCOUNT_APPROVED = "approved"
CAFE_ACCOUNT_REJECTED = "rejected"
CAFE_ACCOUNT_SUSPENDED = "suspended"


class CafePartnerAuthError(ValueError):
    """Invalid caf? partner input or unavailable credentials."""


class CafePartnerAlreadyRegistered(CafePartnerAuthError):
    """Caf? partner email is already registered."""


def _email(value):
    if not isinstance(value, str):
        raise CafePartnerAuthError("Invalid email.")

    normalized = value.strip().lower()

    if (
        len(normalized) > 254
        or not normalized
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
    ):
        raise CafePartnerAuthError("Invalid email.")

    local, domain = normalized.rsplit("@", 1)

    if (
        not local
        or not domain
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        raise CafePartnerAuthError("Invalid email.")

    return normalized


def _password(value):
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= 128
        or any(ord(character) < 32 for character in value)
        or chr(127) in value
    ):
        raise CafePartnerAuthError(
            "Password must contain 12 to 128 valid characters."
        )

    return value


def register_pending_cafe_partner(registration, *, password, now=None):
    """Create a pending partner identity; no approval is implied.

    The caller must separately create and link the pending venue.
    Unique email enforcement requires a MongoDB unique index.
    """
    clean = validate_cafe_registration(registration)
    secret = _password(password)

    timestamp = now or datetime.now(timezone.utc)

    if (
        not isinstance(timestamp, datetime)
        or timestamp.tzinfo is None
        or timestamp.utcoffset() is None
    ):
        raise CafePartnerAuthError("Timezone-aware time required.")

    document = {
        "owner_name": clean["owner_name"],
        "email": _email(clean["owner_email"]),
        "phone": clean["owner_phone"],
        "password_hash": generate_password_hash(
            secret,
            method="scrypt",
        ),
        "account_type": "cafe_partner",
        "status": CAFE_ACCOUNT_PENDING,
        "email_verified": False,
        "venue_id": None,
        "created_at": timestamp.astimezone(timezone.utc),
        "updated_at": timestamp.astimezone(timezone.utc),
    }

    try:
        result = mongo.db.cafe_partner_accounts.insert_one(document)
    except DuplicateKeyError as exc:
        raise CafePartnerAlreadyRegistered(
            "An account with this email already exists."
        ) from exc

    return result.inserted_id


def authenticate_cafe_partner_password(*, email, password):
    """Validate credentials and approval, without issuing a session.

    Failure messages deliberately do not disclose which check failed.
    """
    normalized = _email(email)

    if not isinstance(password, str):
        raise CafePartnerAuthError("Invalid email or password.")

    account = mongo.db.cafe_partner_accounts.find_one({
        "email": normalized,
        "account_type": "cafe_partner",
    })

    if not account:
        raise CafePartnerAuthError("Invalid email or password.")

    stored_hash = account.get("password_hash")

    if (
        not isinstance(stored_hash, str)
        or not check_password_hash(stored_hash, password)
    ):
        raise CafePartnerAuthError("Invalid email or password.")

    if (
        account.get("status") != CAFE_ACCOUNT_APPROVED
        or account.get("email_verified") is not True
        or not isinstance(account.get("venue_id"), ObjectId)
    ):
        raise CafePartnerAuthError(
            "Caf? partner account is not available for login."
        )

    return {
        "partner_id": str(account["_id"]),
        "venue_id": str(account["venue_id"]),
        "account_type": "cafe_partner",
        "email": normalized,
    }


def link_pending_cafe_partner_venue(*, partner_id, venue_id, now=None):
    """Link one pending venue to its original pending partner account.

    The authenticated registration service must supply the identifiers.
    This is an atomic account update, not a two-collection transaction.
    """
    from app.repositories.venue_repository import get_owned_venue

    if (
        not isinstance(partner_id, (str, ObjectId))
        or not ObjectId.is_valid(str(partner_id))
    ):
        raise CafePartnerAuthError("Invalid caf? partner identifier.")

    if (
        not isinstance(venue_id, (str, ObjectId))
        or not ObjectId.is_valid(str(venue_id))
    ):
        raise CafePartnerAuthError("Invalid venue identifier.")

    partner_oid = ObjectId(str(partner_id))
    venue_oid = ObjectId(str(venue_id))

    if now is None:
        timestamp = datetime.now(timezone.utc)
    elif (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafePartnerAuthError("Timezone-aware time required.")
    else:
        timestamp = now.astimezone(timezone.utc)

    venue = get_owned_venue(
        venue_oid,
        owner_id=str(partner_oid),
    )

    if (
        venue is None
        or venue.get("status") != "pending"
        or str(venue.get("owner_id")) != str(partner_oid)
    ):
        raise CafePartnerAuthError(
            "Pending venue does not belong to this caf? partner."
        )

    result = mongo.db.cafe_partner_accounts.update_one(
        {
            "_id": partner_oid,
            "account_type": "cafe_partner",
            "status": CAFE_ACCOUNT_PENDING,
            "venue_id": None,
        },
        {
            "$set": {
                "venue_id": venue_oid,
                "updated_at": timestamp,
            }
        },
    )

    if result.matched_count != 1:
        raise CafePartnerAuthError(
            "Caf? partner account is not eligible for linking."
        )

    return {
        "partner_id": str(partner_oid),
        "venue_id": str(venue_oid),
        "status": CAFE_ACCOUNT_PENDING,
    }
