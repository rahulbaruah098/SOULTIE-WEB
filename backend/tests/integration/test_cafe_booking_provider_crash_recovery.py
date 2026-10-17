"""Crash/retry acceptance for the cafe provider-idempotency adapter."""

from unittest.mock import patch

import pytest
from bson import ObjectId

from app.services import cafe_booking_recipient_outbox_worker_service as worker
from app.services.cafe_booking_recipient_provider_idempotency_service import (
    CafeProviderIdempotencyError,
)


def _parent(token):
    reservation = str(ObjectId())
    return {
        "event_id": str(ObjectId()),
        "reservation_id": reservation,
        "venue_id": str(ObjectId()),
        "customer_id": str(ObjectId()),
        "event_key": reservation + ":booking_confirmed",
        "event_type": "booking_confirmed",
        "lease_token": token,
        "attempt_count": 1,
    }


def _recipient(parent, identity, token, attempt):
    return {
        "delivery_key": (
            parent["event_key"] + ":customer:" + identity
        ),
        "recipient_type": "customer",
        "recipient_id": identity,
        "lease_token": token,
        "attempt_count": attempt,
    }


class DurableFakeProvider:
    supports_durable_idempotency = True

    def __init__(self):
        self.calls = []
        self.accepted = set()

    def send_once(
        self, *, idempotency_key, parent_claim, recipient_claim
    ):
        self.calls.append(idempotency_key)
        self.accepted.add(idempotency_key)
        return True


def test_provider_acceptance_survives_simulated_worker_crash():
    original = _parent("a" * 64)
    recipient_id = str(ObjectId())
    first = _recipient(original, recipient_id, "b" * 64, 1)

    recovered_parent = dict(original)
    recovered_parent["lease_token"] = "c" * 64
    recovered_parent["attempt_count"] = 2

    retry = _recipient(
        recovered_parent, recipient_id, "d" * 64, 2
    )

    provider = DurableFakeProvider()
    observations = []

    def simulated_coordinator(*, claim, deliver_recipient):
        current = first if claim["attempt_count"] == 1 else retry
        assert deliver_recipient(claim, current) is True

        if claim["attempt_count"] == 1:
            raise RuntimeError(
                "Simulated crash after durable provider acceptance "
                "and before MongoDB receipt"
            )

        return {
            "event_id": claim["event_id"],
            "outcome": "delivered",
            "all_delivered": True,
            "accepted_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
        }

    observations.extend([original, recovered_parent])

    with patch.object(
        worker,
        "claim_next_cafe_booking_event",
        side_effect=observations,
    ), patch.object(
        worker,
        "process_claimed_cafe_booking_recipients",
        side_effect=simulated_coordinator,
    ):
        with pytest.raises(RuntimeError, match="Simulated crash"):
            worker.process_one_cafe_booking_outbox_event_with_provider(
                provider=provider
            )

        result = worker.process_one_cafe_booking_outbox_event_with_provider(
            provider=provider
        )

    assert result["outcome"] == "delivered"
    assert len(provider.calls) == 2
    assert provider.calls[0] == provider.calls[1]
    assert len(provider.accepted) == 1


def test_unsafe_provider_cannot_claim_parent():
    with patch.object(worker, "claim_next_cafe_booking_event") as claim:
        with pytest.raises(CafeProviderIdempotencyError):
            worker.process_one_cafe_booking_outbox_event_with_provider(
                provider=object()
            )
        claim.assert_not_called()


def test_empty_queue_never_calls_provider():
    provider = DurableFakeProvider()

    with patch.object(
        worker, "claim_next_cafe_booking_event", return_value=None
    ):
        result = worker.process_one_cafe_booking_outbox_event_with_provider(
            provider=provider
        )

    assert result == {"outcome": "empty"}
    assert provider.calls == []
