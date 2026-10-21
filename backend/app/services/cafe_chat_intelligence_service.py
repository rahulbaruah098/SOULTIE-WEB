"""Deterministic, privacy-conscious cafe recognition.

No database access, external model calls, message persistence,
booking mutations or automatic chat responses.
"""

import re
import unicodedata
from difflib import SequenceMatcher

MAX_MESSAGE_LENGTH = 1500
MAX_CANDIDATES = 500

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

INTENT_WORDS = {
    "coffee": frozenset({
        "coffee", "cafe", "cafes", "chai", "tea", "coffeeshop",
    }),
    "meeting": frozenset({
        "meet", "meeting", "meetup", "hangout", "hang",
    }),
    "date": frozenset({
        "date", "dinner", "lunch", "brunch",
    }),
}

_NEGATIONS = (
    "do not", "don t", "dont", "not meeting",
    "not interested", "never", "avoid",
    "cancel", "no thanks", "cannot", "can t",
)

_GENERIC = frozenset({
    "cafe", "coffee", "restaurant", "house",
    "tea", "shop", "the", "and", "bar",
})


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_TOKEN.findall(value))


def detect_chat_venue_intent(message):
    if not isinstance(message, str):
        raise ValueError("Message must be text.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError("Message exceeds limit.")

    text = normalize_text(message)
    tokens = set(text.split())

    intents = [
        category for category, words in INTENT_WORDS.items()
        if tokens.intersection(words)
    ]

    if "catch up" in text and "meeting" not in intents:
        intents.append("meeting")

    # Recognize complete phrases after punctuation normalization.
    padded = " " + text + " "
    negated = any(
        " " + phrase + " " in padded
        for phrase in _NEGATIONS
    )

    return {
        "has_venue_intent": bool(intents) and not negated,
        "intents": intents,
        "negated": negated,
    }


def _name_score(message, name):
    text = normalize_text(message)
    title = normalize_text(name)

    if not text or not title:
        return 0.0

    parts = title.split()
    anchors = [part for part in parts if part not in _GENERIC]

    if not anchors:
        return 0.0

    if re.search(r"(?<!\w)" + re.escape(title) + r"(?!\w)", text):
        return 1.0

    words = text.split()

    anchor_score = max(
        (
            SequenceMatcher(None, anchor, word, autojunk=False).ratio()
            for anchor in anchors
            for word in words
        ),
        default=0.0,
    )

    if anchor_score < 0.80:
        return 0.0

    best = 0.0
    for width in {max(1, len(parts) - 1), len(parts), len(parts) + 1}:
        if width > len(words):
            continue
        for index in range(len(words) - width + 1):
            phrase = " ".join(words[index:index + width])
            score = SequenceMatcher(
                None, title, phrase, autojunk=False
            ).ratio()
            best = max(best, score)

    return best


def recommend_chat_venues(message, candidate_venues, *, limit=5):
    intent = detect_chat_venue_intent(message)

    if not isinstance(candidate_venues, (list, tuple)):
        raise ValueError("Candidates must be a list.")

    if len(candidate_venues) > MAX_CANDIDATES:
        raise ValueError("Too many venue candidates.")

    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Invalid suggestion limit.")

    if intent["negated"]:
        return {"intent": intent, "suggestions": []}

    suggestions = []
    seen = set()

    for venue in candidate_venues:
        if not isinstance(venue, dict):
            continue
        if venue.get("status") != "approved":
            continue
        if venue.get("active") is not True:
            continue

        name = next(
            (
                value.strip()
                for field in ("name", "display_name", "venue_name")
                for value in [venue.get(field)]
                if isinstance(value, str) and value.strip()
            ),
            "",
        )

        identifier = venue.get("_id", venue.get("id"))
        if not name or identifier is None:
            continue

        identifier = str(identifier)
        if identifier in seen:
            continue
        seen.add(identifier)

        score = _name_score(message, name)

        # This milestone recognizes named venues only.
        # Generic-intent recommendations are built in 2L.2.
        if score < 0.86:
            continue

        suggestions.append({
            "venue_id": identifier,
            "name": name,
            "match_score": round(score, 3),
            "name_mentioned": True,
        })

    suggestions.sort(
        key=lambda item: (
            -item["match_score"],
            item["name"].casefold(),
            item["venue_id"],
        )
    )

    return {
        "intent": intent,
        "suggestions": suggestions[:limit],
    }