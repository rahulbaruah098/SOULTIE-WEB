"""Internal Soultie cafe booking notification outbox dispatcher.

This module is deliberately NOT a scheduled worker or HTTP endpoint.

The caller must supply a trusted server-owned delivery adapter.
No client-supplied callable, event identity, or provider credential
may reach this interface.

A successful adapter return value of exactly True is required before
recording delivery. Any other result is a delivery failure.

External delivery is inherently at-least-once after worker crashes.
Before production, the adapter must implement downstream idempotency
using the durable event_key and verified recipient routing.

Never log raw provider responses, credentials, or private user data.
"""

from collections.abc import Callable

from app.repositories.cafe_booking_outbox_delivery_repository import (
    claim_next_cafe_booking_event,
)
from app.repositories.cafe_booking_outbox_receipt_repository import (
    acknowledge_cafe_booking_delivery,
    record_cafe_booking_delivery_failure,
)


class CafeBookingDispatcherError(RuntimeError):
    """Invalid dispatcher contract or acknowledgement."""


class CafeBookingDeliveryFailure(Exception):
    """A trusted delivery adapter reported a categorized failure."""

    def __init__(self, code="DELIVERY_FAILED"):
        if (
            not isinstance(code, str)
            or code not in {
                "DELIVERY_FAILED",
                "PROVIDER_UNAVAILABLE",
                "RECIPIENT_UNAVAILABLE",
                "CHANNEL_UNAVAILABLE",
                "DELIVERY_TIMEOUT",
            }
        ):
            code = "DELIVERY_FAILED"
        self.code = code
        super().__init__(code)


def dispatch_one_cafe_booking_event(*, deliver):
    """Process at most one queued booking notification.

    The adapter receives a validated, currently leased event claim.
    It must confirm durable downstream acceptance and return True.
    It must be idempotent by event_key.

    Any database claim or receipt error propagates. Do not report
    success when an acknowledgement cannot be persisted.
    """
    if not isinstance(deliver, Callable):
        raise CafeBookingDispatcherError(
            "A trusted notification delivery adapter is required."
        )

    claim = claim_next_cafe_booking_event()

    if claim is None:
        return {"outcome": "empty"}

    if (
        not isinstance(claim, dict)
        or not isinstance(claim.get("event_id"), str)
        or not isinstance(claim.get("event_key"), str)
        or not isinstance(claim.get("lease_token"), str)
        or type(claim.get("attempt_count")) is not int
        or claim["attempt_count"] < 1
    ):
        raise CafeBookingDispatcherError(
            "Invalid notification lease claim."
        )

    receipt = {
        "event_id": claim["event_id"],
        "lease_token": claim["lease_token"],
        "attempt_count": claim["attempt_count"],
    }

    try:
        accepted = deliver(claim)
        if accepted is not True:
            raise CafeBookingDeliveryFailure()
    except CafeBookingDeliveryFailure as exc:
        failed = record_cafe_booking_delivery_failure(
            **receipt,
            failure_code=exc.code,
        )
        if (
            not isinstance(failed, dict)
            or failed.get("delivery_state")
            not in {"pending", "dead_letter"}
        ):
            raise CafeBookingDispatcherError(
                "Invalid notification failure receipt."
            )

        return {
            "outcome": failed["delivery_state"],
            "event_id": claim["event_id"],
        }
    except Exception:
        # Suppress raw adapter error details and store only the safe
        # machine-readable category. Database receipt failures below
        # are intentionally allowed to propagate.
        failed = record_cafe_booking_delivery_failure(
            **receipt,
            failure_code="DELIVERY_FAILED",
        )
        if (
            not isinstance(failed, dict)
            or failed.get("delivery_state")
            not in {"pending", "dead_letter"}
        ):
            raise CafeBookingDispatcherError(
                "Invalid notification failure receipt."
            )
        return {
            "outcome": failed["delivery_state"],
            "event_id": claim["event_id"],
        }

    acknowledged = acknowledge_cafe_booking_delivery(
        **receipt,
    )

    if (
        not isinstance(acknowledged, dict)
        or acknowledged.get("event_id") != claim["event_id"]
        or acknowledged.get("delivery_state") != "delivered"
    ):
        raise CafeBookingDispatcherError(
            "Invalid successful delivery receipt."
        )

    return {
        "outcome": "delivered",
        "event_id": claim["event_id"],
    }
