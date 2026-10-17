"""Permanent mocked recipient-aware outbox worker entry tests.

No live MongoDB, scheduler, provider, or network operation.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from pymongo.errors import OperationFailure

from app.services import (
    cafe_booking_recipient_outbox_worker_service as worker,
)
from app.services.cafe_booking_recipient_dispatch_coordinator import (
    CafeRecipientDispatchError,
)

R = "507f1f77bcf86cd799439099"
V = "507f191e810c19729de860ea"
C = "507f1f77bcf86cd799439011"
EID = "507f1f77bcf86cd799439098"

CLAIM = {
    "event_id": EID,
    "event_key": f"{R}:booking_confirmed",
    "event_type": "booking_confirmed",
    "reservation_id": R,
    "venue_id": V,
    "customer_id": C,
    "lease_token": "a" * 64,
    "attempt_count": 2,
}


@pytest.fixture
def context(monkeypatch):
    acquire = Mock(return_value=deepcopy(CLAIM))
    coordinate = Mock(return_value={
        "event_id": EID,
        "outcome": "incomplete",
        "all_delivered": False,
        "accepted_count": 1,
        "failed_count": 1,
        "skipped_count": 0,
    })
    monkeypatch.setattr(
        worker, "claim_next_cafe_booking_event", acquire
    )
    monkeypatch.setattr(
        worker,
        "process_claimed_cafe_booking_recipients",
        coordinate,
    )
    adapter = Mock(return_value=True)
    return acquire, coordinate, adapter


def run(context):
    return worker.process_one_cafe_booking_outbox_event(
        deliver_recipient=context[2]
    )


def test_empty_queue_does_not_dispatch(context):
    acquire, coordinate, adapter = context
    acquire.return_value = None
    assert run(context) == {"outcome": "empty"}
    coordinate.assert_not_called()
    adapter.assert_not_called()


@pytest.mark.parametrize("outcome,delivered", [
    ("incomplete", False),
    ("blocked", False),
    ("delivered", True),
])
def test_valid_coordinator_result_passed_through(
    context, outcome, delivered
):
    acquire, coordinate, adapter = context
    expected = {
        "event_id": EID,
        "outcome": outcome,
        "all_delivered": delivered,
        "accepted_count": 1,
        "failed_count": 0,
        "skipped_count": 1,
    }
    coordinate.return_value = expected

    assert run(context) == expected
    coordinate.assert_called_once_with(
        claim=CLAIM,
        deliver_recipient=adapter,
    )


@pytest.mark.parametrize("invalid", [
    None, False, 0, "provider", [], {},
])
def test_invalid_adapter_rejected_before_claim(
    context, invalid
):
    acquire, coordinate, _ = context
    with pytest.raises(worker.CafeRecipientOutboxWorkerError):
        worker.process_one_cafe_booking_outbox_event(
            deliver_recipient=invalid
        )
    acquire.assert_not_called()
    coordinate.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("event_id", "invalid"),
    ("event_key", "forged"),
    ("event_type", "unknown"),
    ("reservation_id", "invalid"),
    ("venue_id", "invalid"),
    ("customer_id", "invalid"),
    ("lease_token", "invalid"),
    ("attempt_count", 0),
    ("attempt_count", True),
])
def test_forged_parent_claim_rejected(
    context, field, value
):
    acquire, coordinate, adapter = context
    acquire.return_value = {**CLAIM, field: value}

    with pytest.raises(CafeRecipientDispatchError):
        run(context)

    coordinate.assert_not_called()
    adapter.assert_not_called()


@pytest.mark.parametrize("invalid", [
    None,
    {},
    {"event_id": "wrong", "outcome": "delivered",
     "all_delivered": True, "accepted_count": 2,
     "failed_count": 0, "skipped_count": 0},
    {"event_id": EID, "outcome": "delivered",
     "all_delivered": False, "accepted_count": 1,
     "failed_count": 0, "skipped_count": 1},
    {"event_id": EID, "outcome": "incomplete",
     "all_delivered": True, "accepted_count": 1,
     "failed_count": 1, "skipped_count": 0},
    {"event_id": EID, "outcome": "unknown",
     "all_delivered": False, "accepted_count": 0,
     "failed_count": 0, "skipped_count": 2},
    {"event_id": EID, "outcome": "blocked",
     "all_delivered": False, "accepted_count": -1,
     "failed_count": 0, "skipped_count": 2},
    {"event_id": EID, "outcome": "blocked",
     "all_delivered": False, "accepted_count": True,
     "failed_count": 0, "skipped_count": 2},
])
def test_bad_coordinator_outcome_rejected(context, invalid):
    _, coordinate, _ = context
    coordinate.return_value = invalid
    with pytest.raises(worker.CafeRecipientOutboxWorkerError):
        run(context)


def test_claim_database_failure_propagates(context):
    acquire, coordinate, _ = context
    acquire.side_effect = OperationFailure("database unavailable")
    with pytest.raises(OperationFailure):
        run(context)
    coordinate.assert_not_called()


def test_coordinator_database_failure_propagates(context):
    _, coordinate, _ = context
    coordinate.side_effect = OperationFailure(
        "recipient receipt database unavailable"
    )
    with pytest.raises(OperationFailure):
        run(context)


def test_coordinator_exception_not_reported_as_success(context):
    _, coordinate, _ = context
    coordinate.side_effect = CafeRecipientDispatchError(
        "invalid recipient claim"
    )
    with pytest.raises(CafeRecipientDispatchError):
        run(context)


def test_does_not_invoke_adapter_directly(context):
    _, coordinate, adapter = context
    result = run(context)
    assert result["outcome"] == "incomplete"
    adapter.assert_not_called()
    coordinate.assert_called_once()


def test_worker_module_does_not_import_legacy_dispatcher():
    from pathlib import Path

    source = Path(worker.__file__).read_text(
        encoding="utf-8-sig"
    )
    assert "cafe_booking_outbox_dispatcher_service" not in source
    assert "dispatch_one_cafe_booking_event" not in source
