"""Recipient-aware Soultie cafe booking notification coordination.

This is an internal, unscheduled service. No HTTP endpoint or
notification provider is enabled.

Use only with a trusted, currently leased parent outbox claim and
a server-owned, idempotent recipient delivery adapter.

A recipient marked delivered is never resent by this coordinator.
Only verified recipient receipts allow parent completion.

Incomplete events remain unacknowledged at parent level. Their
existing parent lease may expire and be reclaimed by a future worker.
An eventual worker must implement backoff, lease-duration checks,
dead-letter escalation and idempotency across worker crashes.
"""

from collections.abc import Callable

from bson import ObjectId

from app.services.cafe_booking_recipient_routing_service import (
    resolve_cafe_booking_recipients,
)
from app.repositories.cafe_booking_recipient_delivery_repository import (
    ensure_cafe_booking_recipient_deliveries,
)
from app.repositories.cafe_booking_recipient_claim_repository import (
    claim_cafe_booking_recipient_delivery,
)
from app.repositories.cafe_booking_recipient_receipt_repository import (
    acknowledge_cafe_booking_recipient_delivery,
    record_cafe_booking_recipient_failure,
)
from app.services.cafe_booking_outbox_completion_service import (
    complete_cafe_booking_outbox_if_ready,
)


class CafeRecipientDispatchError(RuntimeError):
    """Invalid notification dispatch contract."""


class CafeRecipientProviderFailure(Exception):
    """A trusted adapter reported an allowed delivery failure."""

    ALLOWED = frozenset({
        "DELIVERY_FAILED",
        "PROVIDER_UNAVAILABLE",
        "RECIPIENT_UNAVAILABLE",
        "CHANNEL_UNAVAILABLE",
        "DELIVERY_TIMEOUT",
    })

    def __init__(self, code="DELIVERY_FAILED"):
        if not isinstance(code, str) or code not in self.ALLOWED:
            code = "DELIVERY_FAILED"
        self.code = code
        super().__init__(code)


def _verify_parent_claim(claim):
    if not isinstance(claim, dict):
        raise CafeRecipientDispatchError(
            "Trusted parent claim required."
        )

    event_id = claim.get("event_id")
    reservation_id = claim.get("reservation_id")
    venue_id = claim.get("venue_id")
    customer_id = claim.get("customer_id")
    event_type = claim.get("event_type")
    lease_token = claim.get("lease_token")
    attempt_count = claim.get("attempt_count")

    for label, value in (
        ("event_id", event_id),
        ("reservation_id", reservation_id),
        ("venue_id", venue_id),
        ("customer_id", customer_id),
    ):
        if (
            not isinstance(value, str)
            or not ObjectId.is_valid(value)
            or str(ObjectId(value)) != value
        ):
            raise CafeRecipientDispatchError(
                "Invalid parent notification identity."
            )

    if (
        event_type not in {
            "booking_created",
            "booking_confirmed",
            "booking_rejected",
            "booking_cancelled",
            "booking_completed",
            "booking_no_show",
        }
        or claim.get("event_key")
        != f"{reservation_id}:{event_type}"
        or not isinstance(lease_token, str)
        or len(lease_token) != 64
        or any(c not in "0123456789abcdef" for c in lease_token)
        or type(attempt_count) is not int
        or attempt_count < 1
    ):
        raise CafeRecipientDispatchError(
            "Invalid parent notification lease."
        )


def process_claimed_cafe_booking_recipients(
    *, claim, deliver_recipient
):
    """Process one leased parent across its required recipients.

    Adapter signature: deliver_recipient(parent_claim, recipient_claim).
    Only literal True means downstream durable acceptance.

    Provider errors become safe recipient failure categories.
    Database errors are deliberately propagated.
    """
    if not isinstance(deliver_recipient, Callable):
        raise CafeRecipientDispatchError(
            "Trusted recipient delivery adapter required."
        )

    _verify_parent_claim(claim)

    routing = resolve_cafe_booking_recipients(event=claim)

    if (
        not isinstance(routing, dict)
        or routing.get("event_key") != claim["event_key"]
        or routing.get("event_type") != claim["event_type"]
    ):
        raise CafeRecipientDispatchError(
            "Recipient routing does not match parent claim."
        )

    initialized = ensure_cafe_booking_recipient_deliveries(
        routing=routing
    )

    if (
        not isinstance(initialized, dict)
        or initialized.get("event_key") != claim["event_key"]
        or initialized.get("event_type") != claim["event_type"]
        or not isinstance(initialized.get("recipients"), list)
        or len(initialized["recipients"])
        != len(routing.get("recipients", []))
    ):
        raise CafeRecipientDispatchError(
            "Recipient initialization was not verified."
        )

    expected = {
        item["delivery_key"]: item
        for item in routing["recipients"]
    }
    observed = set()
    accepted_count = 0
    failed_count = 0
    skipped_count = 0

    for recipient in initialized["recipients"]:
        if not isinstance(recipient, dict):
            raise CafeRecipientDispatchError(
                "Invalid initialized recipient."
            )

        key = recipient.get("delivery_key")
        original = expected.get(key)

        if (
            original is None
            or key in observed
            or recipient.get("recipient_type")
            != original["recipient_type"]
            or recipient.get("recipient_id")
            != original["recipient_id"]
            or recipient.get("delivery_state")
            not in {
                "pending", "processing",
                "delivered", "dead_letter",
            }
        ):
            raise CafeRecipientDispatchError(
                "Initialized recipient identity mismatch."
            )

        observed.add(key)

    if observed != set(expected):
        raise CafeRecipientDispatchError(
            "Missing required notification recipients."
        )

    for recipient in initialized["recipients"]:
        if recipient["delivery_state"] in {
            "delivered", "dead_letter"
        }:
            skipped_count += 1
            continue

        key = recipient["delivery_key"]

        individual_claim = claim_cafe_booking_recipient_delivery(
            delivery_key=key
        )

        if individual_claim is None:
            skipped_count += 1
            continue

        if (
            not isinstance(individual_claim, dict)
            or individual_claim.get("delivery_key") != key
            or individual_claim.get("recipient_type")
            != recipient["recipient_type"]
            or individual_claim.get("recipient_id")
            != recipient["recipient_id"]
            or not isinstance(
                individual_claim.get("lease_token"), str
            )
            or len(individual_claim["lease_token"]) != 64
            or type(
                individual_claim.get("attempt_count")
            ) is not int
            or individual_claim["attempt_count"] < 1
        ):
            raise CafeRecipientDispatchError(
                "Invalid individual recipient claim."
            )

        receipt = {
            "delivery_key": key,
            "lease_token": individual_claim["lease_token"],
            "attempt_count": individual_claim["attempt_count"],
        }

        failure_code = None

        try:
            accepted = deliver_recipient(claim, individual_claim)
            if accepted is not True:
                failure_code = "DELIVERY_FAILED"
        except CafeRecipientProviderFailure as exc:
            failure_code = exc.code
        except Exception:
            failure_code = "DELIVERY_FAILED"

        if failure_code is None:
            result = acknowledge_cafe_booking_recipient_delivery(
                **receipt
            )
            if (
                not isinstance(result, dict)
                or result.get("delivery_key") != key
                or result.get("delivery_state") != "delivered"
            ):
                raise CafeRecipientDispatchError(
                    "Recipient success receipt invalid."
                )
            accepted_count += 1
        else:
            result = record_cafe_booking_recipient_failure(
                **receipt, failure_code=failure_code
            )
            if (
                not isinstance(result, dict)
                or result.get("delivery_key") != key
                or result.get("delivery_state")
                not in {"pending", "dead_letter"}
            ):
                raise CafeRecipientDispatchError(
                    "Recipient failure receipt invalid."
                )
            failed_count += 1

    completion = complete_cafe_booking_outbox_if_ready(
        claim=claim, routing=routing
    )

    if (
        not isinstance(completion, dict)
        or completion.get("event_id") != claim["event_id"]
        or completion.get("outcome")
        not in {"delivered", "incomplete", "blocked"}
        or (
            completion.get("all_delivered") is True
        ) != (
            completion.get("outcome") == "delivered"
        )
    ):
        raise CafeRecipientDispatchError(
            "Invalid parent completion result."
        )

    return {
        "event_id": claim["event_id"],
        "outcome": completion["outcome"],
        "all_delivered": completion["all_delivered"],
        "accepted_count": accepted_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
    }
