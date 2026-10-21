"""Conversation-authorized cafe recommendation boundary.

A trusted caller supplies the authenticated user identity.
Chat membership is verified before any venue or availability
lookup. No chat content is persisted or sent externally.
"""

from datetime import date
from bson import ObjectId

from app.services.chat_service import get_my_conversation
from app.services.cafe_chat_booking_action_service import (
    build_live_chat_cafe_cards,
)

MAX_CHAT_TEXT_LENGTH = 1500


def get_authorized_chat_cafe_suggestions(
    *,
    user_id,
    conversation_id,
    message,
    booking_date=None,
    party_size=None,
    limit=5,
):
    """Return cafe cards only for an authorized chat participant.

    Authentication itself belongs to the Flask route layer.
    This service requires the route to obtain user_id from
    its validated authenticated principal, never request JSON.
    """
    if not isinstance(user_id, ObjectId):
        raise ValueError("Authenticated user ID is required.")

    if not isinstance(conversation_id, str):
        raise ValueError("Invalid conversation ID.")

    if not ObjectId.is_valid(conversation_id):
        raise ValueError("Invalid conversation ID.")

    if not isinstance(message, str):
        raise ValueError("Message must be text.")

    if not 1 <= len(message) <= MAX_CHAT_TEXT_LENGTH:
        raise ValueError("Message length is invalid.")

    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("Invalid suggestion limit.")

    if (booking_date is None) != (party_size is None):
        raise ValueError(
            "Booking date and party size are required together."
        )

    if booking_date is not None:
        if type(booking_date) is not date:
            raise ValueError("Invalid booking date.")
        if type(party_size) is not int or not 1 <= party_size <= 100:
            raise ValueError("Invalid party size.")

    # Security boundary: no recommendation computation or
    # availability lookup can precede this authorization.
    get_my_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
    )

    result = build_live_chat_cafe_cards(
        message,
        booking_date=booking_date,
        party_size=party_size,
        limit=limit,
    )

    # Do not expose the conversation record or other member data.
    return {
        "conversation_id": conversation_id,
        "recommendations": result,
        "booking_created": False,
    }