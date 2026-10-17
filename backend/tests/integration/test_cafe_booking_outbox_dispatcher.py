"""Permanent mocked Xuoroni cafe booking dispatcher safety tests.

No real MongoDB, notification provider, email, or network delivery.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.services import (
    cafe_booking_outbox_dispatcher_service as svc,
)


CLAIM = {
    "event_id": "507f1f77bcf86cd799439099",
    "event_key": (
        "507f1f77bcf86cd799439099:booking_created"
    ),
    "reservation_id": "507f1f77bcf86cd799439099",
    "venue_id": "507f191e810c19729de860ea",
    "customer_id": "507f1f77bcf86cd799439011",
    "event_type": "booking_created",
    "lease_token": "a" * 64,
    "attempt_count": 1,
}


@pytest.fixture
def context(monkeypatch):
    claim = Mock(return_value=deepcopy(CLAIM))
    acknowledgement = Mock(return_value={
        "event_id": CLAIM["event_id"],
        "delivery_state": "delivered",
    })
    failure = Mock(return_value={
        "event_id": CLAIM["event_id"],
        "delivery_state": "pending",
    })
    monkeypatch.setattr(
        svc, "claim_next_cafe_booking_event", claim
    )
    monkeypatch.setattr(
        svc, "acknowledge_cafe_booking_delivery",
        acknowledgement,
    )
    monkeypatch.setattr(
        svc, "record_cafe_booking_delivery_failure", failure
    )
    return claim, acknowledgement, failure


def test_success_requires_true_and_acknowledges(context):
    claim, acknowledgement, failure = context
    adapter = Mock(return_value=True)

    result = svc.dispatch_one_cafe_booking_event(
        deliver=adapter,
    )

    assert result == {
        "outcome": "delivered",
        "event_id": CLAIM["event_id"],
    }
    claim.assert_called_once_with()
    adapter.assert_called_once_with(CLAIM)
    acknowledgement.assert_called_once_with(
        event_id=CLAIM["event_id"],
        lease_token=CLAIM["lease_token"],
        attempt_count=1,
    )
    failure.assert_not_called()


@pytest.mark.parametrize(
    "response", [False, None, 0, 1, "success", {}, []]
)
def test_non_true_delivery_requires_failure_receipt(
    context, response
):
    _, acknowledgement, failure = context
    result = svc.dispatch_one_cafe_booking_event(
        deliver=lambda _: response,
    )

    assert result["outcome"] == "pending"
    acknowledgement.assert_not_called()
    failure.assert_called_once_with(
        event_id=CLAIM["event_id"],
        lease_token=CLAIM["lease_token"],
        attempt_count=1,
        failure_code="DELIVERY_FAILED",
    )


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
def test_categorized_failure_receives_safe_code(context, code):
    _, acknowledgement, failure = context

    def adapter(_):
        raise svc.CafeBookingDeliveryFailure(code)

    result = svc.dispatch_one_cafe_booking_event(
        deliver=adapter,
    )

    assert result["outcome"] == "pending"
    acknowledgement.assert_not_called()
    assert failure.call_args.kwargs["failure_code"] == code


def test_unknown_failure_code_is_normalized(context):
    _, acknowledgement, failure = context

    def adapter(_):
        raise svc.CafeBookingDeliveryFailure(
            "sensitive private provider response"
        )

    result = svc.dispatch_one_cafe_booking_event(
        deliver=adapter,
    )

    assert result["outcome"] == "pending"
    acknowledgement.assert_not_called()
    assert failure.call_args.kwargs[
        "failure_code"
    ] == "DELIVERY_FAILED"


def test_unexpected_adapter_exception_is_sanitized(context):
    _, acknowledgement, failure = context

    def adapter(_):
        raise RuntimeError("private account or token")

    result = svc.dispatch_one_cafe_booking_event(
        deliver=adapter,
    )

    assert result["outcome"] == "pending"
    acknowledgement.assert_not_called()
    assert failure.call_args.kwargs[
        "failure_code"
    ] == "DELIVERY_FAILED"


def test_dead_letter_receipt_is_reported(context):
    _, acknowledgement, failure = context
    failure.return_value = {
        "delivery_state": "dead_letter"
    }

    result = svc.dispatch_one_cafe_booking_event(
        deliver=lambda _: False,
    )

    assert result["outcome"] == "dead_letter"
    acknowledgement.assert_not_called()


def test_empty_queue_does_not_deliver(context):
    claim, acknowledgement, failure = context
    claim.return_value = None
    adapter = Mock(return_value=True)

    assert svc.dispatch_one_cafe_booking_event(
        deliver=adapter,
    ) == {"outcome": "empty"}

    adapter.assert_not_called()
    acknowledgement.assert_not_called()
    failure.assert_not_called()


@pytest.mark.parametrize(
    "invalid", [None, False, 123, "adapter", {}, []]
)
def test_invalid_adapter_rejected_before_claim(
    context, invalid
):
    claim, acknowledgement, failure = context

    with pytest.raises(svc.CafeBookingDispatcherError):
        svc.dispatch_one_cafe_booking_event(
            deliver=invalid,
        )

    claim.assert_not_called()
    acknowledgement.assert_not_called()
    failure.assert_not_called()


@pytest.mark.parametrize(
    "alterations",
    [
        {"event_id": None},
        {"event_key": None},
        {"lease_token": None},
        {"attempt_count": 0},
        {"attempt_count": True},
        {"attempt_count": "1"},
    ],
)
def test_invalid_claim_rejected_before_delivery(
    context, alterations
):
    claim, acknowledgement, failure = context
    claim.return_value = {**CLAIM, **alterations}
    adapter = Mock(return_value=True)

    with pytest.raises(svc.CafeBookingDispatcherError):
        svc.dispatch_one_cafe_booking_event(
            deliver=adapter,
        )

    adapter.assert_not_called()
    acknowledgement.assert_not_called()
    failure.assert_not_called()


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {},
        {"event_id": "wrong", "delivery_state": "delivered"},
        {
            "event_id": CLAIM["event_id"],
            "delivery_state": "pending",
        },
    ],
)
def test_invalid_success_receipt_fails_closed(
    context, receipt
):
    _, acknowledgement, failure = context
    acknowledgement.return_value = receipt

    with pytest.raises(svc.CafeBookingDispatcherError):
        svc.dispatch_one_cafe_booking_event(
            deliver=lambda _: True,
        )

    failure.assert_not_called()


@pytest.mark.parametrize(
    "receipt", [None, {}, {"delivery_state": "delivered"}]
)
def test_invalid_failure_receipt_fails_closed(
    context, receipt
):
    _, acknowledgement, failure = context
    failure.return_value = receipt

    with pytest.raises(svc.CafeBookingDispatcherError):
        svc.dispatch_one_cafe_booking_event(
            deliver=lambda _: False,
        )

    acknowledgement.assert_not_called()


def test_claim_database_error_propagates(context):
    claim, acknowledgement, failure = context
    claim.side_effect = RuntimeError("database unavailable")
    adapter = Mock()

    with pytest.raises(RuntimeError):
        svc.dispatch_one_cafe_booking_event(
            deliver=adapter,
        )

    adapter.assert_not_called()
    acknowledgement.assert_not_called()
    failure.assert_not_called()


def test_success_receipt_database_error_propagates(context):
    _, acknowledgement, failure = context
    acknowledgement.side_effect = RuntimeError(
        "database unavailable"
    )

    with pytest.raises(RuntimeError):
        svc.dispatch_one_cafe_booking_event(
            deliver=lambda _: True,
        )

    failure.assert_not_called()


def test_failure_receipt_database_error_propagates(context):
    _, acknowledgement, failure = context
    failure.side_effect = RuntimeError(
        "database unavailable"
    )

    with pytest.raises(RuntimeError):
        svc.dispatch_one_cafe_booking_event(
            deliver=lambda _: False,
        )

    acknowledgement.assert_not_called()
