"""Read-only, approved-cafe chat recommendations.

The caller is responsible for authenticating and authorizing
access to the conversation. Never accept a venue list or
conversation identity directly from an untrusted client.

This module does not save chat messages or create bookings.
"""

from bson import ObjectId
from pymongo.errors import PyMongoError

from app.extensions import mongo
from app.services.cafe_chat_intelligence_service import (
    detect_chat_venue_intent,
    recommend_chat_venues,
    normalize_text,
    MAX_CANDIDATES,
)

MAX_SUGGESTIONS = 5


class CafeChatRecommendationUnavailable(RuntimeError):
    """Approved venue recommendations cannot be retrieved."""


def recommend_verified_chat_cafes(message, *, limit=5):
    """Find eligible venues and return safe, bounded suggestions.

    Generic recommendations are not distance-based or personalized.
    Booking availability must be checked separately.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_SUGGESTIONS:
        raise ValueError("Invalid recommendation limit.")

    intent = detect_chat_venue_intent(message)

    if intent["negated"] or not normalize_text(message):
        return {
            "intent": intent,
            "suggestions": [],
            "recommendation_type": "none",
        }

    projection = {
        "_id": 1,
        "name": 1,
        "display_name": 1,
        "venue_name": 1,
        "status": 1,
        "active": 1,
    }

    try:
        cursor = mongo.db.venues.find(
            {"status": "approved", "active": True},
            projection,
        ).sort([("_id", 1)]).limit(MAX_CANDIDATES)

        records = list(cursor)
    except PyMongoError as exc:
        raise CafeChatRecommendationUnavailable(
            "Verified cafe lookup unavailable."
        ) from exc

    eligible = [
        venue
        for venue in records
        if isinstance(venue, dict)
        and isinstance(venue.get("_id"), ObjectId)
        and venue.get("status") == "approved"
        and venue.get("active") is True
    ]

    matched = recommend_chat_venues(
        message, eligible, limit=limit
    )
    suggestions = matched["suggestions"]

    if suggestions:
        kind = "named_venue"
    elif intent["has_venue_intent"]:
        # Deterministic generic options, not a location-based rank.
        options = []
        seen = set()
        for venue in eligible:
            name = next(
                (
                    value.strip()
                    for key in ("name", "display_name", "venue_name")
                    for value in [venue.get(key)]
                    if isinstance(value, str) and value.strip()
                ),
                "",
            )
            if not name or venue["_id"] in seen:
                continue
            seen.add(venue["_id"])
            options.append({
                "venue_id": str(venue["_id"]),
                "name": name,
                "name_mentioned": False,
                "match_score": 0.0,
            })

        options.sort(
            key=lambda item: (
                item["name"].casefold(),
                item["venue_id"],
            )
        )
        suggestions = options[:limit]
        kind = "general_options" if suggestions else "none"
    else:
        kind = "none"

    return {
        "intent": intent,
        "recommendation_type": kind,
        "suggestions": suggestions,
        "availability_checked": False,
    }