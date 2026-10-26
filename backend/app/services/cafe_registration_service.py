"""Transactional registration for Soultie cafe partners.

Both the account and venue must be inserted in one transaction.
Registration does not verify email, grant approval, or create a login
session. No partial-registration fallback is permitted.
"""

from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from werkzeug.security import generate_password_hash

from app.extensions import mongo
from app.schemas.venue import validate_cafe_registration
from app.services.cafe_partner_auth_service import (
    CafePartnerAlreadyRegistered,
    _email,
    _password,
)


class CafeRegistrationError(ValueError):
    """Invalid cafe registration request or inconsistent persistence."""


def register_cafe_with_venue(*, registration, password):
    """Atomically register a pending owner and their pending cafe.

    Requires MongoDB deployment supporting multi-document transactions.
    All user-controlled values are validated before database writes.
    """
    clean = validate_cafe_registration(registration)
    normalized_email = _email(clean["owner_email"])
    validated_password = _password(password)

    now = datetime.now(timezone.utc)
    partner_id = ObjectId()
    venue_id = ObjectId()

    password_hash = generate_password_hash(
        validated_password,
        method="scrypt",
    )

    account = {
        "_id": partner_id,
        "owner_name": clean["owner_name"],
        "email": normalized_email,
        "phone": clean["owner_phone"],
        "password_hash": password_hash,
        "account_type": "cafe_partner",
        "status": "pending",
        "email_verified": False,
        "venue_id": venue_id,
        "created_at": now,
        "updated_at": now,
    }

    venue = {
        **clean,
        "_id": venue_id,
        "name": clean["venue_name"],
        "owner_id": str(partner_id),
        "status": "pending",
        "status_version": 0,
        "active": False,
        "created_at": now,
        "updated_at": now,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_reason": None,
    }

    def transaction(db_session):
        # Stable IDs ensure callback retries never mix ownership links.
        account_result = mongo.db.cafe_partner_accounts.insert_one(
            dict(account),
            session=db_session,
        )
        if account_result.inserted_id != partner_id:
            raise CafeRegistrationError(
                "Cafe partner registration could not be completed."
            )

        venue_result = mongo.db.venues.insert_one(
            dict(venue),
            session=db_session,
        )
        if venue_result.inserted_id != venue_id:
            raise CafeRegistrationError(
                "Cafe venue registration could not be completed."
            )

        return {
            "partner_id": str(partner_id),
            "venue_id": str(venue_id),
            "status": "pending",
            "email_verified": False,
        }

    try:
        # There is deliberately no unsafe nontransactional fallback.
        with mongo.cx.start_session() as db_session:
            return db_session.with_transaction(transaction)
    except DuplicateKeyError as error:
        raise CafePartnerAlreadyRegistered(
            "An account with this email already exists."
        ) from error
