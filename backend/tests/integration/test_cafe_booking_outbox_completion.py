"""Permanent mocked tests for parent cafe booking notification completion."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

from app.services import cafe_booking_outbox_completion_service as svc

R = "507f1f77bcf86cd799439099"
C = "507f1f77bcf86cd799439011"
P = "507f1f77bcf86cd799439012"
EID = "507f1f77bcf86cd799439098"
EVENT = f"{R}:booking_confirmed"
TOKEN = "a" * 64
NOW = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)

CLAIM = {
    "event_id": EID, "event_key": EVENT,
    "event_type": "booking_confirmed", "reservation_id": R,
    "lease_token": TOKEN, "attempt_count": 2,
}
ROUTING = {
    "event_key": EVENT,
    "event_type": "booking_confirmed",
    "recipients": [
        {
            "recipient_type": role,
            "recipient_id": identity,
            "delivery_key": f"{EVENT}:{role}:{identity}",
        }
        for role, identity in (("customer", C), ("cafe_partner", P))
    ],
}
CK = ROUTING["recipients"][0]["delivery_key"]
PK = ROUTING["recipients"][1]["delivery_key"]


@pytest.fixture
def ctx(monkeypatch):
    records = {}
    for recipient in ROUTING["recipients"]:
        key = recipient["delivery_key"]
        records[key] = {
            "_id": key,
            "event_key": EVENT,
            "event_type": "booking_confirmed",
            "recipient_type": recipient["recipient_type"],
            "recipient_id": ObjectId(recipient["recipient_id"]),
            "delivery_state": "pending",
            "delivered_at": None,
        }
    collection = Mock()
    collection.find_one.side_effect = lambda q, p: deepcopy(records.get(q["_id"]))
    ack = Mock(return_value={"event_id": EID, "delivery_state": "delivered"})
    monkeypatch.setattr(svc, "mongo", SimpleNamespace(
        db=SimpleNamespace(cafe_booking_recipient_deliveries=collection)
    ))
    monkeypatch.setattr(svc, "acknowledge_cafe_booking_delivery", ack)
    return SimpleNamespace(records=records, collection=collection, ack=ack)


def run(claim=None, routing=None):
    return svc.complete_cafe_booking_outbox_if_ready(
        claim=deepcopy(CLAIM if claim is None else claim),
        routing=deepcopy(ROUTING if routing is None else routing),
    )


def delivered(ctx, key):
    ctx.records[key]["delivery_state"] = "delivered"
    ctx.records[key]["delivered_at"] = NOW


def test_pending_prevents_completion(ctx):
    result = run()
    assert result["outcome"] == "incomplete"
    assert result["pending_count"] == 2
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("key", [CK, PK])
def test_one_delivery_insufficient(ctx, key):
    delivered(ctx, key)
    result = run()
    assert result["outcome"] == "incomplete"
    assert result["pending_count"] == 1
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("state", ["pending", "processing"])
def test_unfinished_recipient_blocks(ctx, state):
    delivered(ctx, PK)
    ctx.records[CK]["delivery_state"] = state
    assert run()["all_delivered"] is False
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("key", [CK, PK])
def test_dead_letter_blocks(ctx, key):
    delivered(ctx, PK if key == CK else CK)
    ctx.records[key]["delivery_state"] = "dead_letter"
    result = run()
    assert result["outcome"] == "blocked"
    assert result["dead_letter_count"] == 1
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("key", [CK, PK])
def test_missing_receipt_blocks(ctx, key):
    delivered(ctx, CK)
    delivered(ctx, PK)
    del ctx.records[key]
    assert run()["outcome"] == "incomplete"
    ctx.ack.assert_not_called()


def test_both_verified_receipts_complete_parent(ctx):
    delivered(ctx, CK)
    delivered(ctx, PK)
    assert run() == {
        "event_id": EID,
        "outcome": "delivered",
        "all_delivered": True,
        "pending_count": 0,
        "dead_letter_count": 0,
    }
    ctx.ack.assert_called_once_with(
        event_id=EID, lease_token=TOKEN, attempt_count=2
    )


def test_creation_only_requires_owner(ctx):
    event = f"{R}:booking_created"
    claim = {
        **CLAIM, "event_key": event, "event_type": "booking_created"
    }
    key = f"{event}:cafe_partner:{P}"
    routing = {
        "event_key": event,
        "event_type": "booking_created",
        "recipients": [{
            "recipient_type": "cafe_partner",
            "recipient_id": P,
            "delivery_key": key,
        }],
    }
    ctx.records[key] = {
        "_id": key, "event_key": event,
        "event_type": "booking_created",
        "recipient_type": "cafe_partner",
        "recipient_id": ObjectId(P),
        "delivery_state": "delivered", "delivered_at": NOW,
    }
    assert run(claim=claim, routing=routing)["all_delivered"] is True
    ctx.ack.assert_called_once_with(
        event_id=EID, lease_token=TOKEN, attempt_count=2
    )


@pytest.mark.parametrize("field,value", [
    ("event_id", "invalid"),
    ("event_key", "forged"),
    ("event_type", "booking_created"),
    ("reservation_id", "507f1f77bcf86cd799439097"),
    ("lease_token", "invalid"),
    ("attempt_count", 0),
    ("attempt_count", True),
])
def test_forged_parent_claim_rejected(ctx, field, value):
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run(claim={**CLAIM, field: value})
    ctx.collection.find_one.assert_not_called()
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("change", [
    {"recipients": []},
    {"recipients": [ROUTING["recipients"][0]]},
    {"event_key": "invalid"},
    {"event_type": "booking_created"},
])
def test_incomplete_or_invalid_routing_rejected(ctx, change):
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run(routing={**ROUTING, **change})
    ctx.collection.find_one.assert_not_called()
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("_id", "forged"),
    ("event_key", "forged"),
    ("event_type", "booking_created"),
    ("recipient_type", "cafe_partner"),
    ("recipient_id", ObjectId(P)),
])
def test_persisted_identity_mismatch_rejected(ctx, field, value):
    ctx.records[CK][field] = value
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run()
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("state", ["unknown", None, "sent_unverified"])
def test_unknown_receipt_state_rejected(ctx, state):
    ctx.records[CK]["delivery_state"] = state
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run()
    ctx.ack.assert_not_called()


def test_delivered_without_timestamp_rejected(ctx):
    ctx.records[CK]["delivery_state"] = "delivered"
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run()
    ctx.ack.assert_not_called()


@pytest.mark.parametrize("response", [
    None,
    {},
    {"event_id": "wrong", "delivery_state": "delivered"},
    {"event_id": EID, "delivery_state": "pending"},
])
def test_bad_parent_acknowledgement_rejected(ctx, response):
    delivered(ctx, CK)
    delivered(ctx, PK)
    ctx.ack.return_value = response
    with pytest.raises(svc.CafeBookingOutboxCompletionError):
        run()


def test_recipient_database_failure_propagates(ctx):
    ctx.collection.find_one.side_effect = OperationFailure(
        "database unavailable"
    )
    with pytest.raises(OperationFailure):
        run()
    ctx.ack.assert_not_called()


def test_parent_acknowledgement_failure_propagates(ctx):
    delivered(ctx, CK)
    delivered(ctx, PK)
    ctx.ack.side_effect = OperationFailure("database unavailable")
    with pytest.raises(OperationFailure):
        run()
