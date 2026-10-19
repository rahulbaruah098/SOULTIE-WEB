"""Bounded, explicitly invoked cafe recipient notification worker.

Not scheduled. Not registered with HTTP. Does not select a provider.
External callers must authorize activation and supply the provider.
"""

from app.services.cafe_booking_recipient_outbox_worker_service import (
    process_one_cafe_booking_outbox_event_if_enabled,
)
from app.services.cafe_booking_recipient_provider_idempotency_service import (
    make_cafe_booking_idempotent_adapter,
)


class CafeRecipientRunnerError(RuntimeError):
    """Invalid worker runner configuration or outcome."""


def run_bounded_cafe_booking_recipient_worker(
    *, provider, enabled=False, max_events=1
):
    """Process at most max_events parents; never busy-loop on blockage."""
    if enabled is not True:
        return {
            "outcome": "disabled",
            "processed_count": 0,
            "delivered_count": 0,
        }

    if (
        type(max_events) is not int
        or not 1 <= max_events <= 20
    ):
        raise CafeRecipientRunnerError(
            "max_events must be an integer between 1 and 20."
        )

    # Refuse unsafe providers before the first queue claim.
    make_cafe_booking_idempotent_adapter(provider)

    processed = 0
    delivered = 0
    terminal = "limit_reached"

    for _ in range(max_events):
        result = process_one_cafe_booking_outbox_event_if_enabled(
            provider=provider,
            enabled=True,
        )

        if not isinstance(result, dict):
            raise CafeRecipientRunnerError("Invalid worker result.")

        outcome = result.get("outcome")

        if outcome == "empty":
            terminal = "empty"
            break

        if outcome not in {"delivered", "incomplete", "blocked"}:
            raise CafeRecipientRunnerError(
                "Unknown worker outcome."
            )

        processed += 1

        if outcome == "delivered":
            if result.get("all_delivered") is not True:
                raise CafeRecipientRunnerError(
                    "Inconsistent successful worker outcome."
                )
            delivered += 1
            continue

        if result.get("all_delivered") is not False:
            raise CafeRecipientRunnerError(
                "Inconsistent incomplete worker outcome."
            )

        terminal = outcome
        break

    return {
        "outcome": terminal,
        "processed_count": processed,
        "delivered_count": delivered,
    }
