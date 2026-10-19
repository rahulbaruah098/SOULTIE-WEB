"""Pure retry scheduling policy for cafe recipient notifications.

Calculates decisions only. Never schedules work, accesses MongoDB,
or sends messages. Durable repository rules remain authoritative.
"""

from datetime import datetime, timedelta, timezone


class CafeRecipientRetryPolicyError(ValueError):
    """Invalid retry state or timing input."""


MAX_ATTEMPTS = 8
BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 3600


def calculate_cafe_recipient_retry(*, attempt_count, now):
    """Return a deterministic UTC retry/dead-letter decision.

    attempt_count is the number of attempts already made, including
    the current unsuccessful attempt.
    """
    if type(attempt_count) is not int or attempt_count < 1:
        raise CafeRecipientRetryPolicyError(
            "attempt_count must be a positive integer."
        )

    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise CafeRecipientRetryPolicyError(
            "now must be a timezone-aware datetime."
        )

    current = now.astimezone(timezone.utc)

    if attempt_count >= MAX_ATTEMPTS:
        return {
            "action": "dead_letter",
            "attempt_count": attempt_count,
            "retry_after": None,
            "delay_seconds": None,
        }

    delay = min(
        BASE_DELAY_SECONDS * (2 ** (attempt_count - 1)),
        MAX_DELAY_SECONDS,
    )

    return {
        "action": "retry",
        "attempt_count": attempt_count,
        "retry_after": current + timedelta(seconds=delay),
        "delay_seconds": delay,
    }
