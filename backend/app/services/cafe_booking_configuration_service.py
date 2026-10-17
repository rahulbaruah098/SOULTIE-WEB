"""Authorized internal cafe booking configuration service.

No public route is defined here. The caller must obtain partner_id
and venue_id from an authenticated cafe partner session.

The account is checked once at the application authorization
boundary and again inside the MongoDB transaction. The repository
performs its own owner-bound venue fence and rejects any active
pending or confirmed reservation before writing settings.

No external notification is emitted inside the transaction.
"""

from bson import ObjectId

from app.extensions import mongo
from app.security.cafe_partner_access import authorize_cafe_partner
from app.repositories.cafe_booking_configuration_repository import (
    save_cafe_booking_configuration,
)
from app.services.cafe_reservation_transaction_service import (
    run_cafe_reservation_transaction,
)
from app.schemas.cafe_booking_configuration import (
    validate_cafe_booking_configuration,
)
from app.schemas.cafe_venue_operations import (
    validate_cafe_weekly_hours,
    validate_cafe_booking_settings,
)


class CafeBookingConfigurationAccessDenied(ValueError):
    """Verified owner access is unavailable."""


def _identity(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise CafeBookingConfigurationAccessDenied(
            "Invalid cafe booking configuration identity."
        )
    return str(ObjectId(value))


def update_authorized_cafe_booking_configuration(
    *,
    partner_id,
    venue_id,
    configuration,
    weekly_hours,
    booking_settings,
):
    """Update complete booking settings through a guarded transaction."""
    partner_id = _identity(partner_id)
    venue_id = _identity(venue_id)

    # Validate before starting any database mutation.
    clean = validate_cafe_booking_configuration(configuration)
    hours = validate_cafe_weekly_hours(weekly_hours)
    settings = validate_cafe_booking_settings(booking_settings)

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
        raise CafeBookingConfigurationAccessDenied(
            "Cafe booking configuration access denied."
        )

    def operation(session):
        if (
            session is None
            or getattr(session, "in_transaction", False) is not True
        ):
            raise CafeBookingConfigurationAccessDenied(
                "Active transaction required."
            )

        account = mongo.db.cafe_partner_accounts.find_one(
            {
                "_id": ObjectId(partner_id),
                "account_type": "cafe_partner",
                "status": "approved",
                "email_verified": True,
                "venue_id": ObjectId(venue_id),
            },
            session=session,
        )

        if (
            not isinstance(account, dict)
            or account.get("_id") != ObjectId(partner_id)
            or account.get("account_type") != "cafe_partner"
            or account.get("status") != "approved"
            or account.get("email_verified") is not True
            or account.get("venue_id") != ObjectId(venue_id)
        ):
            raise CafeBookingConfigurationAccessDenied(
                "Cafe partner is not eligible to change configuration."
            )

        return save_cafe_booking_configuration(
            partner_id=partner_id,
            venue_id=venue_id,
            configuration=clean,
            weekly_hours=hours,
            booking_settings=settings,
            session=session,
        )

    return run_cafe_reservation_transaction(operation=operation)
