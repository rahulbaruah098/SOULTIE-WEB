"""Provider idempotency guard for cafe booking recipient notifications.

This module never starts a worker or sends a notification by itself.

The provider must guarantee durable deduplication for repeated calls
with the same idempotency_key, including across provider restarts.

A database lease alone cannot ensure exactly-once external delivery.
"""

from collections.abc import Callable
from app.services.cafe_booking_recipient_dispatch_coordinator import (
    _verify_parent_claim,
    CafeRecipientDispatchError,
)

from app.repositories.cafe_booking_recipient_claim_repository import (
    _delivery_identity,
    CafeRecipientClaimError,
)


class CafeProviderIdempotencyError(RuntimeError):
    """An unsafe provider or recipient delivery contract was supplied."""


def make_cafe_booking_idempotent_adapter(provider):
    """Return the coordinator's two-argument recipient delivery adapter.

    Provider contract:
      supports_durable_idempotency is literally True
      send_once(*, idempotency_key, parent_claim, recipient_claim)

    send_once must return literal True only after durable acceptance.
    The provider must deduplicate the same key across worker crashes.
    """
    if (
        getattr(provider, "supports_durable_idempotency", None)
        is not True
        or not isinstance(getattr(provider, "send_once", None), Callable)
    ):
        raise CafeProviderIdempotencyError(
            "A provider with durable idempotency support is required."
        )

    def deliver_recipient(parent_claim, recipient_claim):
        if not isinstance(parent_claim, dict):
            raise CafeProviderIdempotencyError(
                "A trusted parent claim is required."
            )

        try:
            _verify_parent_claim(parent_claim)
        except CafeRecipientDispatchError as exc:
            raise CafeProviderIdempotencyError(
                "Invalid trusted parent booking claim."
            ) from exc
        if not isinstance(recipient_claim, dict):
            raise CafeProviderIdempotencyError(
                "A trusted recipient claim is required."
            )

        delivery_key = recipient_claim.get("delivery_key")

        try:
            identity = _delivery_identity(delivery_key)
        except CafeRecipientClaimError as exc:
            raise CafeProviderIdempotencyError(
                "Invalid recipient idempotency identity."
            ) from exc

        if (
            identity["event_key"] != parent_claim.get("event_key")
            or identity["event_type"] != parent_claim.get("event_type")
            or identity["recipient_type"]
            != recipient_claim.get("recipient_type")
            or str(identity["recipient_id"])
            != recipient_claim.get("recipient_id")
            or not isinstance(
                recipient_claim.get("lease_token"), str
            )
            or len(recipient_claim["lease_token"]) != 64
            or any(
                char not in "0123456789abcdef"
                for char in recipient_claim["lease_token"]
            )
            or type(recipient_claim.get("attempt_count")) is not int
            or recipient_claim["attempt_count"] < 1
        ):
            raise CafeProviderIdempotencyError(
                "Parent and recipient claim identities do not match."
            )

        # Use the deterministic delivery identity, never a lease token:
        # a recovered worker must reuse precisely the same provider key.
        result = provider.send_once(
            idempotency_key=delivery_key,
            parent_claim=parent_claim,
            recipient_claim=recipient_claim,
        )

        return result is True

    return deliver_recipient