"""Atomic Superadmin moderation of Soultie cafe venues.

Caller must pass an authenticated Superadmin ID, which is verified
again in this service. This service requires MongoDB transactions.
No approval occurs when a transaction cannot be established.
Notification publishing is a separate follow-up integration.
"""

from datetime import datetime, timezone

from bson import ObjectId

from app.extensions import mongo
from app.schemas.venue import VENUE_STATUSES


class CafeModerationError(ValueError):
    """Invalid or ineligible cafe moderation action."""


class CafeModerationConflict(CafeModerationError):
    """A stale decision or inconsistent account/venue relationship."""


TRANSITIONS = {
    "pending": frozenset({"approved", "rejected"}),
    "approved": frozenset({"suspended"}),
    "rejected": frozenset({"approved"}),
    "suspended": frozenset({"approved"}),
}


def _oid(value, label):
    if (
        not isinstance(value, (str, ObjectId))
        or not ObjectId.is_valid(str(value))
    ):
        raise CafeModerationError(f"Invalid {label}.")
    return ObjectId(str(value))


def _reason(value, *, required):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise CafeModerationError("Reason must be text.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CafeModerationError("Reason contains control characters.")
    value = value.strip()
    if len(value) > 1000 or (required and not value):
        raise CafeModerationError("A valid reason is required.")
    return value or None


def moderate_cafe_venue(
    *,
    superadmin_id,
    venue_id,
    expected_status,
    decision,
    reason=None,
):
    """Moderate venue and linked account within one Mongo transaction.

    A moderation call must be initiated only by the privileged API
    and must not accept a client-chosen Superadmin identity.
    """
    actor_id = _oid(superadmin_id, "Superadmin ID")
    venue_oid = _oid(venue_id, "venue ID")

    if (
        not isinstance(expected_status, str)
        or expected_status not in VENUE_STATUSES
        or not isinstance(decision, str)
        or decision not in TRANSITIONS[expected_status]
    ):
        raise CafeModerationError("Invalid status transition.")

    safe_reason = _reason(
        reason,
        required=decision in {"rejected", "suspended"},
    )
    now = datetime.now(timezone.utc)

    def operation(session):
        admin = mongo.db.admins.find_one(
            {"_id": actor_id},
            session=session,
        )
        if (
            not admin
            or admin.get("role") != "super_admin"
            or admin.get("is_active") is False
            or admin.get("status", "active") != "active"
        ):
            raise CafeModerationError("Superadmin access required.")

        venue = mongo.db.venues.find_one(
            {"_id": venue_oid},
            session=session,
        )
        if not venue or venue.get("status") != expected_status:
            raise CafeModerationConflict("Venue state has changed.")

        owner_value = venue.get("owner_id")
        owner_oid = _oid(owner_value, "linked owner ID")

        account = mongo.db.cafe_partner_accounts.find_one(
            {
                "_id": owner_oid,
                "account_type": "cafe_partner",
                "venue_id": venue_oid,
            },
            session=session,
        )

        if (
            not account
            or account.get("_id") != owner_oid
            or account.get("venue_id") != venue_oid
            or str(venue.get("owner_id")) != str(account.get("_id"))
            or account.get("account_type") != "cafe_partner"
            or account.get("status") != expected_status
        ):
            raise CafeModerationConflict(
                "Linked account does not match venue ownership or status."
            )

        # Approval is forbidden until the linked owner proves
        # control of the registered email address.
        if decision == "approved" and account.get("email_verified") is not True:
            raise CafeModerationConflict(
                "Verified cafe partner email is required for approval."
            )

        venue_result = mongo.db.venues.update_one(
            {
                "_id": venue_oid,
                "owner_id": str(owner_oid),
                "status": expected_status,
            },
            {
                "$set": {
                    "status": decision,
                    "reviewed_at": now,
                    "reviewed_by": str(actor_id),
                    "review_reason": safe_reason,
                    "updated_at": now,
                },
                "$inc": {"status_version": 1},
            },
            session=session,
        )

        if venue_result.matched_count != 1:
            raise CafeModerationConflict(
                "Venue was changed concurrently."
            )

        account_result = mongo.db.cafe_partner_accounts.update_one(
            {
                "_id": owner_oid,
                "account_type": "cafe_partner",
                "venue_id": venue_oid,
                "status": expected_status,
            },
            {
                "$set": {
                    "status": decision,
                    "updated_at": now,
                }
            },
            session=session,
        )

        if account_result.matched_count != 1:
            raise CafeModerationConflict(
                "Linked account was changed concurrently."
            )

        mongo.db.audit_logs.insert_one(
            {
                "actor_type": "super_admin",
                "actor_id": str(actor_id),
                "resource_type": "cafe_venue",
                "resource_id": str(venue_oid),
                "action": "cafe_moderation",
                "previous_status": expected_status,
                "new_status": decision,
                "reason": safe_reason,
                "created_at": now,
            },
            session=session,
        )

        return {
            "venue_id": str(venue_oid),
            "partner_id": str(owner_oid),
            "previous_status": expected_status,
            "status": decision,
        }

    # No silent fallback: multi-document consistency is mandatory.
    with mongo.cx.start_session() as db_session:
        return db_session.with_transaction(operation)
