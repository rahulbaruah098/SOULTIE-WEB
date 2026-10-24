"""Phase 2M Safe Meet authorization and proposal preparation.

This module prepares proposals but does not persist them, book
tables, or expose a customer API. Reservation linking remains
disabled until ownership is verified against Phase 2K records.
"""

from datetime import datetime, timezone
from bson import ObjectId
from flask import current_app

from app.extensions import mongo
from app.services.safe_meet_lifecycle_service import (
    SafeMeetLifecycleError,
    create_safe_meet_proposal,
)


class SafeMeetAuthorizationError(PermissionError):
    """Meetup proposal failed a required access or eligibility check."""


def prepare_authorized_safe_meet_proposal(
    *,
    user_id,
    conversation_id,
    planned_at,
    now,
    venue_id=None,
    reservation_id=None,
):
    """Verify chat membership and optional cafe before proposal creation."""
    from app.services.chat_service import get_my_conversation
    from app.repositories.chat_repository import (
        get_conversation_by_id_for_user,
    )

    if not isinstance(user_id, ObjectId):
        raise SafeMeetAuthorizationError("Invalid authenticated identity.")

    if type(conversation_id) is not str or not ObjectId.is_valid(
        conversation_id
    ):
        raise SafeMeetAuthorizationError("Invalid conversation identifier.")

    # Critical: apply the existing eligibility and access checks first.
    get_my_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
    )

    record = get_conversation_by_id_for_user(
        ObjectId(conversation_id),
        user_id,
    )

    if not isinstance(record, dict):
        raise SafeMeetAuthorizationError("Conversation is unavailable.")

    members = record.get("member_ids")
    if (
        not isinstance(members, list)
        or len(members) != 2
        or not all(isinstance(item, ObjectId) for item in members)
        or len(set(members)) != 2
        or user_id not in members
    ):
        raise SafeMeetAuthorizationError(
            "Conversation is not eligible for a Safe Meet."
        )

    reservation_oid = None
    if reservation_id is not None:
        if venue_id is None:
            raise SafeMeetAuthorizationError(
                "Reservation linking requires a venue."
            )
        if (
            type(reservation_id) is not str
            or len(reservation_id) != 24
            or not ObjectId.is_valid(reservation_id)
        ):
            raise SafeMeetAuthorizationError(
                "Invalid reservation identifier."
            )
        reservation_oid = ObjectId(reservation_id)

    if venue_id is not None:
        if type(venue_id) is not str or not ObjectId.is_valid(venue_id):
            raise SafeMeetAuthorizationError("Invalid venue identifier.")

        venue = mongo.db.venues.find_one(
            {
                "_id": ObjectId(venue_id),
                "status": "approved",
                "active": True,
            },
            {"_id": 1},
        )

        if venue is None:
            raise SafeMeetAuthorizationError(
                "Venue is not approved and active."
            )

    if reservation_oid is not None:
        reservation = mongo.db.reservations.find_one(
            {
                "_id": reservation_oid,
                "customer_id": user_id,
                "venue_id": ObjectId(venue_id),
                "status": "confirmed",
            },
            {
                "_id": 1,
                "customer_id": 1,
                "venue_id": 1,
                "status": 1,
                "start_at": 1,
                "end_at": 1,
                "party_size": 1,
            },
        )

        if not isinstance(reservation, dict):
            raise SafeMeetAuthorizationError(
                "Eligible customer reservation not found."
            )

        start_at = reservation.get("start_at")
        end_at = reservation.get("end_at")

        if (
            reservation.get("_id") != reservation_oid
            or reservation.get("customer_id") != user_id
            or reservation.get("venue_id") != ObjectId(venue_id)
            or reservation.get("status") != "confirmed"
            or type(reservation.get("party_size")) is not int
            or reservation["party_size"] < 2
            or not isinstance(start_at, datetime)
            or start_at.tzinfo is None
            or not isinstance(end_at, datetime)
            or end_at.tzinfo is None
            or not isinstance(planned_at, datetime)
            or planned_at.tzinfo is None
            or start_at.astimezone(timezone.utc)
               != planned_at.astimezone(timezone.utc)
            or end_at <= start_at
        ):
            raise SafeMeetAuthorizationError(
                "Reservation does not match the proposed meetup."
            )

    return create_safe_meet_proposal(
        conversation_id=conversation_id,
        proposer_id=str(user_id),
        participant_ids=[str(item) for item in members],
        planned_at=planned_at,
        venue_id=venue_id,
        reservation_id=(
            str(reservation_oid)
            if reservation_oid is not None
            else None
        ),
        now=now,
    )
