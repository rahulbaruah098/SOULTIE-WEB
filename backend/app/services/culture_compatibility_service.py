"""Explainable compatibility using voluntarily shared interests."""

WEIGHTS = {
    "languages": 20,
    "festivals": 10,
    "food_interests": 15,
    "music_interests": 10,
    "lifestyle": 20,
    "relationship_goals": 25,
}


class CompatibilityInputError(ValueError):
    pass


def _choices(profile, field):
    value = profile.get(field, [])

    if not isinstance(value, list) or len(value) > 20:
        raise CompatibilityInputError("Invalid preference list.")

    result = set()

    for item in value:
        if not isinstance(item, str):
            raise CompatibilityInputError(
                "Preferences must contain text."
            )

        cleaned = " ".join(item.strip().casefold().split())

        if (
            not cleaned
            or len(cleaned) > 60
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in item)
        ):
            raise CompatibilityInputError(
                "Invalid preference value."
            )

        result.add(cleaned)

    return result


def calculate_culture_compatibility(first, second):
    if not isinstance(first, dict) or not isinstance(second, dict):
        raise CompatibilityInputError("Two profiles are required.")

    details = {}
    earned = 0.0
    possible = 0

    for field, weight in WEIGHTS.items():
        left = _choices(first, field)
        right = _choices(second, field)

        if not left or not right:
            details[field] = {
                "status": "insufficient_information",
                "shared": [],
                "score": None,
            }
            continue

        shared = sorted(left & right)
        similarity = len(shared) / len(left | right)

        earned += similarity * weight
        possible += weight

        details[field] = {
            "status": "compared",
            "shared": shared[:10],
            "score": round(similarity * 100, 1),
        }

    if possible == 0:
        return {
            "compatibility_score": None,
            "confidence": "insufficient_information",
            "compared_weight": 0,
            "explanation": details,
        }

    score = round(100 * earned / possible, 1)
    coverage = (
        "high_coverage" if possible >= 75
        else "moderate_coverage" if possible >= 40
        else "low_coverage"
    )

    return {
        "compatibility_score": score,
        "confidence": coverage,
        "compared_weight": possible,
        "explanation": details,
    }
