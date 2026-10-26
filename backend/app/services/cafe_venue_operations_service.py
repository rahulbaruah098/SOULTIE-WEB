"""Authorized service for Soultie cafe venue operations.

Facility-only writes are enabled for now. Weekly hours and
booking settings are blocked until confirmed reservation
conflict checks and safe update sequencing are implemented.

This module does not authenticate HTTP sessions. The API must
derive partner and venue IDs from a verified cafe session.
"""

from bson import ObjectId

from app.repositories.cafe_venue_operations_repository import (
    update_owned_cafe_operations,
)
from app.schemas.cafe_venue_operations import (
    validate_cafe_operations_update,
)
from app.security.cafe_partner_access import authorize_cafe_partner


class CafeVenueOperationsDenied(ValueError):
    """Cafe owner access or operations write denied."""


class CafeVenueOperationsConflict(ValueError):
    """Booking-sensitive operations update not yet permitted."""


def _identity(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeVenueOperationsDenied(
            "Cafe operations access denied."
        )
    return str(ObjectId(value))


def update_cafe_owner_operations(*, partner_id, venue_id, changes):
    """Submit an authorized facility update for an active cafe.

    Status, active flag, ownership, and moderation fields are
    never client-writable. Reservation-sensitive settings are
    deliberately denied until the conflict guard exists.
    """
    partner_id = _identity(partner_id)
    venue_id = _identity(venue_id)

    context = authorize_cafe_partner(
        partner_id=partner_id,
        venue_id=venue_id,
    )

    if (
        not isinstance(context, dict)
        or context.get("account_type") != "cafe_partner"
        or context.get("partner_id") != partner_id
        or context.get("venue_id") != venue_id
    ):
        raise CafeVenueOperationsDenied(
            "Cafe operations access denied."
        )

    clean = validate_cafe_operations_update(changes)

    if "weekly_hours" in clean or "booking_settings" in clean:
        raise CafeVenueOperationsConflict(
            "Booking-sensitive settings require conflict checks."
        )

    result = update_owned_cafe_operations(
        partner_id=partner_id,
        venue_id=venue_id,
        changes=clean,
    )

    if result is None:
        raise CafeVenueOperationsDenied(
            "Cafe operations access denied."
        )

    if (
        not isinstance(result, dict)
        or str(result.get("_id")) != venue_id
        or result.get("owner_id") != partner_id
        or result.get("status") != "approved"
        or result.get("active") is not True
        or result.get("facilities") != clean["facilities"]
    ):
        raise CafeVenueOperationsDenied(
            "Cafe operations update cannot be verified."
        )

    return {
        "venue_id": venue_id,
        "facilities": list(clean["facilities"]),
        "status": "approved",
        "message": "Cafe facilities updated.",
    }
