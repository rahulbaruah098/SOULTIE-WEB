"""Transaction-scoped dating-customer eligibility for cafe bookings.

This checks an existing authenticated dating user's CURRENT database
status. It does not authenticate an HTTP token or session.

The API layer must obtain customer_id exclusively from Soultie's
verified dating-user authentication context, never from a client
supplied JSON field.

Cafe partner identities use a separate collection and cannot pass
this eligibility check merely by having a valid partner session.

Every booking attempt and transaction retry must recheck this
database-backed eligibility using the same MongoDB session.
"""

from bson import ObjectId

from app.extensions import mongo


class CafeBookingCustomerAccessError(ValueError):
    """Customer booking eligibility cannot be evaluated safely."""


def authorize_cafe_booking_customer(
    customer_id,
    venue_id,
    session,
):
    """Return True only for an active Soultie dating-user record.

    Return False for missing or non-active users. Raise on malformed
    identities, absent transactions or inconsistent database data.

    This function does not verify a login credential, venue status,
    mutual blocks or customer/venue policy. Those checks belong at
    their respective authenticated application boundaries.
    """
    if (
        session is None
        or getattr(session, "in_transaction", False) is not True
    ):
        raise CafeBookingCustomerAccessError(
            "An active booking transaction is required."
        )

    if (
        not isinstance(customer_id, str)
        or not ObjectId.is_valid(customer_id)
        or not isinstance(venue_id, str)
        or not ObjectId.is_valid(venue_id)
    ):
        raise CafeBookingCustomerAccessError(
            "Invalid booking identity."
        )

    customer_oid = ObjectId(customer_id)

    user = mongo.db.users.find_one(
        {
            "_id": customer_oid,
            "account_status": "active",
        },
        session=session,
    )

    if user is None:
        return False

    if not isinstance(user, dict):
        raise CafeBookingCustomerAccessError(
            "Invalid customer account record."
        )

    if (
        user.get("_id") != customer_oid
        or user.get("account_status") != "active"
    ):
        raise CafeBookingCustomerAccessError(
            "Customer account verification failed."
        )

    # If an explicit account type exists, reject identities that are
    # not regular dating users. Legacy dating records may omit it.
    account_type = user.get("account_type")
    if account_type not in (None, "user", "dating_user"):
        return False

    return True
