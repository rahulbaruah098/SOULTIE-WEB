"""Safe booking actions for verified chat cafe suggestions.

The service never creates reservations, checks availability
without a requested date, or treats chat as booking consent.
The client must call the existing Phase 2K APIs.
"""

from bson import ObjectId

from app.services.cafe_chat_venue_recommendation_service import (
    recommend_verified_chat_cafes,
)

MAX_CARDS = 5


def _valid_object_id(value):
    if not isinstance(value, str):
        return False
    return len(value) == 24 and ObjectId.is_valid(value)


def build_chat_cafe_booking_cards(message, *, limit=5):
    """Provide bounded, actionable cafe suggestions.

    Caller must verify authorization for the conversation.
    None of these actions itself implies a booking.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CARDS:
        raise ValueError("Invalid card limit.")

    recommendations = recommend_verified_chat_cafes(
        message,
        limit=limit,
    )

    if not isinstance(recommendations, dict):
        raise ValueError("Invalid venue recommendation response.")

    raw = recommendations.get("suggestions")
    if not isinstance(raw, list) or len(raw) > limit:
        raise ValueError("Invalid venue recommendation list.")

    cards = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid venue recommendation.")

        venue_id = item.get("venue_id")
        name = item.get("name")

        if not _valid_object_id(venue_id):
            raise ValueError("Invalid trusted venue identifier.")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid trusted venue name.")

        if venue_id in seen:
            continue

        seen.add(venue_id)

        if item.get("name_mentioned") is not True:
            mentioned = False
        else:
            mentioned = True

        cards.append({
            "venue_id": venue_id,
            "name": name.strip(),
            "name_mentioned": mentioned,
            "action_label": "Book a place in this cafe",
            "booking_requires_user_confirmation": True,
            "availability_checked": False,
            "availability_slots": None,
            "api": {
                "venue_details": {
                    "method": "GET",
                    "path": "/api/v1/venues/" + venue_id,
                    "authentication_required": False,
                },
                "check_availability": {
                    "method": "GET",
                    "path": (
                        "/api/v1/venues/" + venue_id
                        + "/availability"
                    ),
                    "query_parameters": [
                        "date",
                        "party_size",
                    ],
                    "authentication_required": False,
                },
                "create_booking": {
                    "method": "POST",
                    "path": "/api/v1/venues/bookings",
                    "authentication_required": True,
                    "required_body_fields": [
                        "venue_id",
                        "start_at",
                        "party_size",
                        "idempotency_key",
                    ],
                },
            },
        })

    return {
        "intent": recommendations.get("intent"),
        "recommendation_type": recommendations.get(
            "recommendation_type", "none"
        ),
        "cards": cards,
        "availability_checked": False,
        "booking_created": False,
    }



def build_live_chat_cafe_cards(
    message,
    *,
    booking_date=None,
    party_size=None,
    limit=5,
):
    """Enrich verified suggestions with real Phase 2K availability.

    Availability is evaluated only when both booking parameters are
    supplied. No reservation is created; user confirmation remains
    mandatory. Requires an active Flask application context.
    """
    from datetime import date as Date
    from flask import current_app

    if (booking_date is None) != (party_size is None):
        raise ValueError("Date and party size must be provided together.")

    if booking_date is not None:
        if type(booking_date) is not Date:
            raise ValueError("Invalid booking date.")
        if type(party_size) is not int or not 1 <= party_size <= 100:
            raise ValueError("Invalid party size.")

    response = build_chat_cafe_booking_cards(message, limit=limit)

    if booking_date is None or not response["cards"]:
        return response

    # Reuse the established Phase 2K public availability endpoint.
    # The returned payload comes directly from its authoritative
    # MongoDB-backed capacity evaluation.
    client = current_app.test_client()

    for card in response["cards"]:
        venue_id = card["venue_id"]
        path = "/api/v1/venues/" + venue_id + "/availability"

        result = client.get(
            path,
            query_string={
                "date": booking_date.isoformat(),
                "party_size": str(party_size),
            },
        )

        if result.status_code == 200:
            payload = result.get_json(silent=True)

            if (
                not isinstance(payload, dict)
                or payload.get("venue_id") != venue_id
                or payload.get("date") != booking_date.isoformat()
                or payload.get("party_size") != party_size
                or payload.get("advisory") is not True
                or not isinstance(payload.get("timezone"), str)
                or not isinstance(payload.get("slots"), list)
            ):
                raise CafeChatAvailabilityUnavailable(
                    "Invalid live availability response."
                )

            slots = payload["slots"]
            card["availability_checked"] = True
            card["availability"] = payload
            card["availability_slots"] = slots
            card["availability_status"] = (
                "available" if slots else "no_slots"
            )
            card["booking_action_enabled"] = bool(slots)

        elif result.status_code == 404:
            card["availability_checked"] = False
            card["availability"] = None
            card["availability_slots"] = None
            card["availability_status"] = "not_bookable"
            card["booking_action_enabled"] = False

        else:
            # Never misrepresent backend failure as no free tables.
            raise CafeChatAvailabilityUnavailable(
                "Live venue availability could not be verified."
            )

    response["availability_checked"] = all(
        card["availability_checked"] for card in response["cards"]
    )
    return response


class CafeChatAvailabilityUnavailable(RuntimeError):
    """Existing availability endpoint could not be reached successfully."""
