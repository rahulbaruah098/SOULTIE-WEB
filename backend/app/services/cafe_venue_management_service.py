"""Owner-scoped, read-only cafe venue management.

Cafe owner sessions are separate from dating-user JWTs and
Superadmin credentials. Every read checks live partner/venue state.
"""

from bson import ObjectId

from app.repositories.venue_repository import get_owned_venue
from app.security.cafe_partner_access import authorize_cafe_partner


class CafeVenueManagementDenied(PermissionError):
    """The cafe venue is not available to this partner."""


PUBLIC_PROFILE_FIELDS = frozenset({
    "venue_name",
    "description",
    "address",
    "city",
    "state",
    "postal_code",
    "contact_phone",
    "website",
})


def get_cafe_owner_venue_profile(*, partner_id, venue_id):
    """Return an allowlisted profile for the authorized cafe owner.

    This operation does not modify venue status, ownership,
    approval, availability, or bookings.
    """
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
        raise CafeVenueManagementDenied("Cafe venue access denied.")

    venue = get_owned_venue(
        venue_id,
        owner_id=partner_id,
    )

    if (
        not isinstance(venue, dict)
        or venue.get("_id") != ObjectId(venue_id)
        or venue.get("owner_id") != partner_id
        or venue.get("status") != "approved"
        or venue.get("active") is not True
    ):
        raise CafeVenueManagementDenied("Cafe venue access denied.")

    profile = {
        "venue_id": str(venue["_id"]),
        "status": "approved",
        "active": True,
    }

    for field in sorted(PUBLIC_PROFILE_FIELDS):
        value = venue.get(field)
        profile[field] = (
            value if isinstance(value, str) else None
        )

    if not profile["venue_name"]:
        raise CafeVenueManagementDenied("Cafe venue access denied.")

    return profile

def update_cafe_owner_venue_profile(
    *, partner_id, venue_id, changes
):
    """Update an owned cafe and return it to Superadmin review.

    The existing repository owns field validation, ownership
    restrictions, and the transition back to pending.
    Never accept client-provided partner or venue ownership.
    """
    from app.repositories.venue_repository import (
        update_owned_venue_profile,
    )

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
        raise CafeVenueManagementDenied(
            "Cafe venue access denied."
        )

    if (
        not isinstance(partner_id, str)
        or not ObjectId.is_valid(partner_id)
        or not isinstance(venue_id, str)
        or not ObjectId.is_valid(venue_id)
    ):
        raise CafeVenueManagementDenied(
            "Cafe venue access denied."
        )

    # IMPORTANT: The existing repository validates the exact
    # allowlisted PROFILE_FIELDS and atomically targets the
    # requested owner's eligible venue. It resets status to
    # "pending", so the old approval is never silently reused.
    result = update_owned_venue_profile(
        venue_id,
        owner_id=partner_id,
        changes=changes,
    )

    if result is None:
        raise CafeVenueManagementDenied(
            "Cafe venue access denied."
        )

    return {
        "venue_id": venue_id,
        "status": "pending",
        "requires_reapproval": True,
        "message": "Venue changes submitted for review.",
    }
