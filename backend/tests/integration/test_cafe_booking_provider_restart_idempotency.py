"""Durable provider restart and multi-recipient idempotency contracts.

Uses a shared in-memory durable-store simulation; no network or DB.
This does NOT certify an external notification provider.
"""

from copy import deepcopy

import pytest
from bson import ObjectId

from app.services.cafe_booking_recipient_provider_idempotency_service import (
    CafeProviderIdempotencyError,
    make_cafe_booking_idempotent_adapter,
)


def _claims():
    reservation = str(ObjectId())
    customer = str(ObjectId())
    partner = str(ObjectId())
    event_type = "booking_confirmed"
    event_key = f"{reservation}:{event_type}"

    parent = {
        "event_id": str(ObjectId()),
        "reservation_id": reservation,
        "venue_id": str(ObjectId()),
        "customer_id": customer,
        "event_type": event_type,
        "event_key": event_key,
        "lease_token": "a" * 64,
        "attempt_count": 1,
    }

    recipients = [
        {
            "delivery_key": f"{event_key}:customer:{customer}",
            "recipient_type": "customer",
            "recipient_id": customer,
            "lease_token": "b" * 64,
            "attempt_count": 1,
        },
        {
            "delivery_key": f"{event_key}:cafe_partner:{partner}",
            "recipient_type": "cafe_partner",
            "recipient_id": partner,
            "lease_token": "c" * 64,
            "attempt_count": 1,
        },
    ]

    return parent, recipients


class RestartableFakeProvider:
    supports_durable_idempotency = True

    def __init__(self, persisted_keys):
        self.persisted_keys = persisted_keys
        self.attempted_keys = []
        self.newly_accepted_keys = []

    def send_once(
        self, *, idempotency_key, parent_claim, recipient_claim
    ):
        assert idempotency_key == recipient_claim["delivery_key"]
        assert parent_claim["event_key"] in idempotency_key

        self.attempted_keys.append(idempotency_key)

        if idempotency_key not in self.persisted_keys:
            self.persisted_keys.add(idempotency_key)
            self.newly_accepted_keys.append(idempotency_key)

        return True


def test_two_recipients_receive_distinct_durable_keys():
    parent, recipients = _claims()
    persisted = set()
    provider = RestartableFakeProvider(persisted)
    deliver = make_cafe_booking_idempotent_adapter(provider)

    assert deliver(parent, recipients[0]) is True
    assert deliver(parent, recipients[1]) is True

    assert len(persisted) == 2
    assert len(provider.newly_accepted_keys) == 2
    assert recipients[0]["delivery_key"] != recipients[1]["delivery_key"]


def test_provider_restart_deduplicates_both_recipients():
    parent, recipients = _claims()
    persisted = set()

    first_provider = RestartableFakeProvider(persisted)
    first_delivery = make_cafe_booking_idempotent_adapter(first_provider)

    for recipient in recipients:
        assert first_delivery(parent, recipient) is True

    # Simulate a new process and new database leases after the
    # provider accepted messages but before local receipt recording.
    recovered_parent = deepcopy(parent)
    recovered_parent["lease_token"] = "d" * 64
    recovered_parent["attempt_count"] = 2

    recovered_recipients = deepcopy(recipients)
    for i, recipient in enumerate(recovered_recipients):
        recipient["lease_token"] = ("e" if i == 0 else "f") * 64
        recipient["attempt_count"] = 2

    restarted_provider = RestartableFakeProvider(persisted)
    retry_delivery = make_cafe_booking_idempotent_adapter(restarted_provider)

    for recipient in recovered_recipients:
        assert retry_delivery(recovered_parent, recipient) is True

    assert len(first_provider.newly_accepted_keys) == 2
    assert len(restarted_provider.attempted_keys) == 2
    assert restarted_provider.newly_accepted_keys == []
    assert len(persisted) == 2


def test_parent_or_recipient_identity_tampering_fails_before_send():
    parent, recipients = _claims()
    provider = RestartableFakeProvider(set())
    deliver = make_cafe_booking_idempotent_adapter(provider)

    altered_parent = deepcopy(parent)
    altered_parent["event_id"] = "invalid"

    with pytest.raises(CafeProviderIdempotencyError):
        deliver(altered_parent, recipients[0])

    altered_recipient = deepcopy(recipients[0])
    altered_recipient["recipient_id"] = str(ObjectId())

    with pytest.raises(CafeProviderIdempotencyError):
        deliver(parent, altered_recipient)

    assert provider.attempted_keys == []


def test_provider_restart_rejects_unsafe_provider():
    class UnsafeProvider:
        supports_durable_idempotency = False

        def send_once(self, **kwargs):
            raise AssertionError("Unsafe provider should never send")

    with pytest.raises(CafeProviderIdempotencyError):
        make_cafe_booking_idempotent_adapter(UnsafeProvider())
