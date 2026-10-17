"""Permanent mocked reliability tests for recipient-aware cafe dispatch.

No real database, background worker, push, email or network operations.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import (
    cafe_booking_recipient_dispatch_coordinator as svc,
)

R = "507f1f77bcf86cd799439099"
C = "507f1f77bcf86cd799439011"
P = "507f1f77bcf86cd799439012"
V = "507f191e810c19729de860ea"
EID = "507f1f77bcf86cd799439098"
E = f"{R}:booking_confirmed"
CK = f"{E}:customer:{C}"
PK = f"{E}:cafe_partner:{P}"

CLAIM = {
    "event_id": EID,
    "event_key": E,
    "event_type": "booking_confirmed",
    "reservation_id": R,
    "venue_id": V,
    "customer_id": C,
    "lease_token": "a" * 64,
    "attempt_count": 1,
}
ROUTING = {
    "event_key": E,
    "event_type": "booking_confirmed",
    "recipients": [
        {
            "delivery_key": CK,
            "recipient_type": "customer",
            "recipient_id": C,
        },
        {
            "delivery_key": PK,
            "recipient_type": "cafe_partner",
            "recipient_id": P,
        },
    ],
}


@pytest.fixture
def context(monkeypatch):
    states = {CK: "pending", PK: "pending"}
    routing = deepcopy(ROUTING)
    route = Mock(return_value=routing)

    def initialize(*, routing):
        return {
            "event_key": routing["event_key"],
            "event_type": routing["event_type"],
            "recipients": [
                {
                    **deepcopy(item),
                    "delivery_state": states[item["delivery_key"]],
                }
                for item in routing["recipients"]
            ],
        }

    ensure = Mock(side_effect=initialize)

    def acquire(*, delivery_key):
        if states[delivery_key] not in {"pending", "processing"}:
            return None
        role = "customer" if delivery_key == CK else "cafe_partner"
        return {
            "delivery_key": delivery_key,
            "recipient_type": role,
            "recipient_id": C if role == "customer" else P,
            "lease_token": "b" * 64,
            "attempt_count": 1,
        }

    claim = Mock(side_effect=acquire)

    def acknowledge(*, delivery_key, lease_token, attempt_count):
        assert lease_token == "b" * 64
        assert attempt_count == 1
        states[delivery_key] = "delivered"
        return {
            "delivery_key": delivery_key,
            "delivery_state": "delivered",
        }

    ack = Mock(side_effect=acknowledge)

    def failure(
        *, delivery_key, lease_token, attempt_count, failure_code
    ):
        assert lease_token == "b" * 64
        assert attempt_count == 1
        assert failure_code in {
            "DELIVERY_FAILED",
            "PROVIDER_UNAVAILABLE",
            "RECIPIENT_UNAVAILABLE",
            "CHANNEL_UNAVAILABLE",
            "DELIVERY_TIMEOUT",
        }
        states[delivery_key] = "pending"
        return {
            "delivery_key": delivery_key,
            "delivery_state": "pending",
        }

    fail = Mock(side_effect=failure)
    parent_ack = Mock()

    def finish(*, claim, routing):
        all_delivered = all(
            states[item["delivery_key"]] == "delivered"
            for item in routing["recipients"]
        )
        if all_delivered:
            parent_ack()
        return {
            "event_id": claim["event_id"],
            "outcome": "delivered" if all_delivered else "incomplete",
            "all_delivered": all_delivered,
        }

    complete = Mock(side_effect=finish)

    monkeypatch.setattr(
        svc, "resolve_cafe_booking_recipients", route
    )
    monkeypatch.setattr(
        svc, "ensure_cafe_booking_recipient_deliveries", ensure
    )
    monkeypatch.setattr(
        svc, "claim_cafe_booking_recipient_delivery", claim
    )
    monkeypatch.setattr(
        svc, "acknowledge_cafe_booking_recipient_delivery", ack
    )
    monkeypatch.setattr(
        svc, "record_cafe_booking_recipient_failure", fail
    )
    monkeypatch.setattr(
        svc, "complete_cafe_booking_outbox_if_ready", complete
    )

    return SimpleNamespace(
        states=states,
        route=route,
        ensure=ensure,
        claim=claim,
        ack=ack,
        fail=fail,
        complete=complete,
        parent_ack=parent_ack,
        routing=routing,
    )


def run(deliver, claim=None):
    return svc.process_claimed_cafe_booking_recipients(
        claim=deepcopy(CLAIM if claim is None else claim),
        deliver_recipient=deliver,
    )


def test_both_recipients_success(context):
    adapter = Mock(return_value=True)
    result = run(adapter)
    assert result["outcome"] == "delivered"
    assert result["all_delivered"] is True
    assert result["accepted_count"] == 2
    assert result["failed_count"] == 0
    assert result["skipped_count"] == 0
    assert context.states == {CK: "delivered", PK: "delivered"}
    assert adapter.call_count == 2
    assert context.parent_ack.call_count == 1


def test_partial_then_retry_skips_delivered_owner(context):
    first = run(
        lambda parent, recipient:
            recipient["recipient_type"] == "cafe_partner"
    )
    assert first["outcome"] == "incomplete"
    assert first["accepted_count"] == 1
    assert first["failed_count"] == 1
    context.parent_ack.assert_not_called()

    sent = []
    second = run(
        lambda parent, recipient:
            sent.append(recipient["delivery_key"]) or True
    )
    assert sent == [CK]
    assert second["outcome"] == "delivered"
    assert second["accepted_count"] == 1
    assert second["skipped_count"] == 1
    context.parent_ack.assert_called_once()


def test_already_delivered_recipients_never_resent(context):
    context.states[CK] = "delivered"
    context.states[PK] = "delivered"
    adapter = Mock(return_value=True)
    result = run(adapter)
    assert result["outcome"] == "delivered"
    assert result["accepted_count"] == 0
    assert result["skipped_count"] == 2
    adapter.assert_not_called()
    context.claim.assert_not_called()


def test_dead_letter_recipient_is_skipped(context):
    context.states[CK] = "dead_letter"
    context.states[PK] = "delivered"
    context.complete.side_effect = None
    context.complete.return_value = {
        "event_id": EID,
        "outcome": "blocked",
        "all_delivered": False,
    }
    adapter = Mock(return_value=True)
    result = run(adapter)
    assert result["outcome"] == "blocked"
    assert result["skipped_count"] == 2
    context.parent_ack.assert_not_called()
    adapter.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [False, None, 0, 1, "accepted", {}, []],
)
def test_nonliteral_true_is_not_delivery_success(context, response):
    result = run(lambda parent, recipient: response)
    assert result["outcome"] == "incomplete"
    assert result["accepted_count"] == 0
    assert result["failed_count"] == 2
    assert context.fail.call_count == 2
    context.ack.assert_not_called()
    context.parent_ack.assert_not_called()


@pytest.mark.parametrize(
    "code",
    [
        "DELIVERY_FAILED",
        "PROVIDER_UNAVAILABLE",
        "RECIPIENT_UNAVAILABLE",
        "CHANNEL_UNAVAILABLE",
        "DELIVERY_TIMEOUT",
    ],
)
def test_trusted_failure_codes_recorded(context, code):
    def adapter(parent, recipient):
        raise svc.CafeRecipientProviderFailure(code)

    result = run(adapter)
    assert result["failed_count"] == 2
    assert result["all_delivered"] is False
    assert all(
        call.kwargs["failure_code"] == code
        for call in context.fail.call_args_list
    )
    context.parent_ack.assert_not_called()


def test_unknown_provider_failure_code_sanitized(context):
    def adapter(parent, recipient):
        raise svc.CafeRecipientProviderFailure(
            "private contact information"
        )

    result = run(adapter)
    assert result["failed_count"] == 2
    assert all(
        call.kwargs["failure_code"] == "DELIVERY_FAILED"
        for call in context.fail.call_args_list
    )


def test_unexpected_provider_exception_sanitized(context):
    def adapter(parent, recipient):
        raise RuntimeError("private notification payload")

    result = run(adapter)
    assert result["failed_count"] == 2
    assert all(
        call.kwargs["failure_code"] == "DELIVERY_FAILED"
        for call in context.fail.call_args_list
    )


def test_unavailable_individual_claim_skips_delivery(context):
    context.claim.return_value = None
    context.claim.side_effect = None
    adapter = Mock(return_value=True)
    result = run(adapter)
    assert result["outcome"] == "incomplete"
    assert result["skipped_count"] == 2
    adapter.assert_not_called()
    context.ack.assert_not_called()


@pytest.mark.parametrize("invalid", [None, False, 5, "adapter"])
def test_invalid_adapter_rejected_before_routing(context, invalid):
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(invalid)
    context.route.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", "invalid"),
        ("event_key", "forged"),
        ("event_type", "unsupported"),
        ("reservation_id", "invalid"),
        ("venue_id", "invalid"),
        ("customer_id", "invalid"),
        ("lease_token", "invalid"),
        ("attempt_count", 0),
        ("attempt_count", True),
    ],
)
def test_invalid_parent_claim_rejected_before_routing(
    context, field, value
):
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(lambda *_: True, claim={**CLAIM, field: value})
    context.route.assert_not_called()


def test_invalid_routing_rejected_before_initialization(context):
    context.route.return_value = {
        **ROUTING, "event_key": "forged"
    }
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(lambda *_: True)
    context.ensure.assert_not_called()


@pytest.mark.parametrize("replacement", [
    [],
    [{"delivery_key": CK, "recipient_type": "customer",
      "recipient_id": C, "delivery_state": "pending"}],
    [
        {"delivery_key": CK, "recipient_type": "customer",
         "recipient_id": C, "delivery_state": "pending"},
        {"delivery_key": CK, "recipient_type": "customer",
         "recipient_id": C, "delivery_state": "pending"},
    ],
])
def test_missing_or_duplicate_initialized_recipient_rejected(
    context, replacement
):
    context.ensure.side_effect = None
    context.ensure.return_value = {
        "event_key": E,
        "event_type": "booking_confirmed",
        "recipients": replacement,
    }
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(lambda *_: True)
    context.claim.assert_not_called()


def test_forged_initialized_recipient_identity_rejected(context):
    context.ensure.side_effect = None
    context.ensure.return_value = {
        "event_key": E,
        "event_type": "booking_confirmed",
        "recipients": [
            {
                **ROUTING["recipients"][0],
                "delivery_state": "pending",
                "recipient_id": P,
            },
            {
                **ROUTING["recipients"][1],
                "delivery_state": "pending",
            },
        ],
    }
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(lambda *_: True)
    context.claim.assert_not_called()


def test_forged_individual_claim_rejected(context):
    context.claim.side_effect = None
    context.claim.return_value = {
        "delivery_key": CK,
        "recipient_type": "cafe_partner",
        "recipient_id": P,
        "lease_token": "b" * 64,
        "attempt_count": 1,
    }
    adapter = Mock(return_value=True)
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(adapter)
    adapter.assert_not_called()


def test_recipient_success_receipt_database_error_propagates(context):
    context.ack.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        run(lambda *_: True)
    context.complete.assert_not_called()


def test_recipient_failure_receipt_database_error_propagates(context):
    context.fail.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        run(lambda *_: False)
    context.complete.assert_not_called()


def test_parent_completion_database_error_propagates(context):
    context.complete.side_effect = RuntimeError(
        "parent database unavailable"
    )
    with pytest.raises(RuntimeError):
        run(lambda *_: True)


@pytest.mark.parametrize(
    "outcome,flag",
    [
        ("delivered", False),
        ("incomplete", True),
        ("unknown", False),
    ],
)
def test_inconsistent_completion_result_rejected(
    context, outcome, flag
):
    context.complete.side_effect = None
    context.complete.return_value = {
        "event_id": EID,
        "outcome": outcome,
        "all_delivered": flag,
    }
    with pytest.raises(svc.CafeRecipientDispatchError):
        run(lambda *_: True)
