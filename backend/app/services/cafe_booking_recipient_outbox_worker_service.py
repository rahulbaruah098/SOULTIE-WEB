"""Internal recipient-aware cafe booking outbox worker entry point.

This module does not start a scheduler, task queue, HTTP endpoint,
or external notification provider.

A trusted caller supplies an idempotent recipient delivery adapter.
The adapter must deduplicate downstream by individual delivery_key.

The recipient coordinator is solely responsible for verifying
recipient delivery and eventual parent completion. This module
never acknowledges parent delivery directly and never calls the
legacy single-recipient-agnostic dispatcher.

An incomplete result leaves the current parent claim unacknowledged.
Future scheduling must account for parent lease expiry, recipient
retry timing, bounded processing, and dead-letter escalation.

Do not activate this entry point in production until real database
concurrency and crash recovery have been verified.
"""

from collections.abc import Callable

from app.repositories.cafe_booking_outbox_delivery_repository import (
    claim_next_cafe_booking_event,
)
from app.services.cafe_booking_recipient_dispatch_coordinator import (
    process_claimed_cafe_booking_recipients,
    _verify_parent_claim,
)


class CafeRecipientOutboxWorkerError(RuntimeError):
    """Invalid recipient-aware parent worker contract."""


_ALLOWED_OUTCOMES = frozenset({
    "delivered", "incomplete", "blocked",
})


def process_one_cafe_booking_outbox_event(*, deliver_recipient):
    """Process at most one parent event through recipient-aware dispatch.

    Returns an empty outcome if no parent is claimable. Any
    persistence or coordination exception propagates so callers
    cannot misreport an unverified delivery as successful.
    """
    if not isinstance(deliver_recipient, Callable):
        raise CafeRecipientOutboxWorkerError(
            "Trusted recipient delivery adapter required."
        )

    claim = claim_next_cafe_booking_event()

    if claim is None:
        return {"outcome": "empty"}

    # Verify the parent lease contract before any recipient activity.
    _verify_parent_claim(claim)

    result = process_claimed_cafe_booking_recipients(
        claim=claim,
        deliver_recipient=deliver_recipient,
    )

    if (
        not isinstance(result, dict)
        or result.get("event_id") != claim["event_id"]
        or result.get("outcome") not in _ALLOWED_OUTCOMES
        or type(result.get("all_delivered")) is not bool
        or result["all_delivered"]
        != (result["outcome"] == "delivered")
        or any(
            type(result.get(key)) is not int
            or result[key] < 0
            for key in (
                "accepted_count",
                "failed_count",
                "skipped_count",
            )
        )
    ):
        raise CafeRecipientOutboxWorkerError(
            "Recipient-aware coordinator returned an invalid outcome."
        )

    return {
        "event_id": result["event_id"],
        "outcome": result["outcome"],
        "all_delivered": result["all_delivered"],
        "accepted_count": result["accepted_count"],
        "failed_count": result["failed_count"],
        "skipped_count": result["skipped_count"],
    }

def process_one_cafe_booking_outbox_event_with_provider(*, provider):
    """Process one event using a durable-idempotency-capable provider.

    The adapter is validated before a parent event can be claimed.
    No scheduler or external provider is activated by this function.
    """
    from app.services.cafe_booking_recipient_provider_idempotency_service import (
        make_cafe_booking_idempotent_adapter,
    )

    adapter = make_cafe_booking_idempotent_adapter(provider)
    return process_one_cafe_booking_outbox_event(
        deliver_recipient=adapter,
    )

def process_one_cafe_booking_outbox_event_if_enabled(
    *, provider, enabled=False
):
    """Fail closed unless an authorized caller explicitly enables work.

    This processes at most one event. It does not configure or launch
    a scheduler and does not establish provider-side idempotency.
    The caller is responsible for authorization and deployment policy.
    """
    if enabled is not True:
        return {
            "outcome": "disabled",
            "all_delivered": False,
        }

    return process_one_cafe_booking_outbox_event_with_provider(
        provider=provider,
    )
