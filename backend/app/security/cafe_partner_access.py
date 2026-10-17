"""Live cafe-partner authorization, independent of user JWTs.

Call after authenticating a cafe-specific session or credential.
Never use dating-user token claims as cafe partner identifiers.
"""

from bson import ObjectId
from bson.errors import InvalidId

from app.extensions import mongo


class CafePartnerAccessDenied(PermissionError):
    """Cafe account or linked venue is not currently authorized."""


def authorize_cafe_partner(*, partner_id, venue_id=None):
    """Return minimal live authorization context for an approved cafe.

    This function does not issue credentials, read browser sessions,
    or grant dating-user and Superadmin permissions.
    """
    if not isinstance(partner_id, str):
        raise CafePartnerAccessDenied("Cafe access denied.")

    try:
        parsed_partner = ObjectId(partner_id)
    except (InvalidId, ValueError, TypeError):
        raise CafePartnerAccessDenied("Cafe access denied.") from None

    account = mongo.db.cafe_partner_accounts.find_one({
        "_id": parsed_partner,
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
    })

    if not isinstance(account, dict):
        raise CafePartnerAccessDenied("Cafe access denied.")

    linked_venue = account.get("venue_id")
    if not isinstance(linked_venue, ObjectId):
        raise CafePartnerAccessDenied("Cafe access denied.")

    if venue_id is not None:
        if not isinstance(venue_id, str):
            raise CafePartnerAccessDenied("Cafe access denied.")
        try:
            requested_venue = ObjectId(venue_id)
        except (InvalidId, TypeError, ValueError):
            raise CafePartnerAccessDenied("Cafe access denied.") from None
        if requested_venue != linked_venue:
            raise CafePartnerAccessDenied("Cafe access denied.")

    # Live, independently enforced venue state. A previously issued
    # session cannot bypass later moderation or suspension.
    venue = mongo.db.venues.find_one({
        "_id": linked_venue,
        "owner_id": str(parsed_partner),
        "status": "approved",
        "active": True,
    })

    if not isinstance(venue, dict):
        raise CafePartnerAccessDenied("Cafe access denied.")

    if account.get("venue_id") != venue.get("_id"):
        raise CafePartnerAccessDenied("Cafe access denied.")

    email = account.get("email")
    if not isinstance(email, str) or not email:
        raise CafePartnerAccessDenied("Cafe access denied.")

    return {
        "partner_id": str(parsed_partner),
        "venue_id": str(linked_venue),
        "email": email,
        "account_type": "cafe_partner",
        "role": "cafe_partner",
    }
