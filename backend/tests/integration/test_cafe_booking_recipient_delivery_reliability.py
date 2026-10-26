"""Permanent mocked Soultie cafe recipient-delivery reliability tests.

Tests validate initialization, recipient isolation, claim fencing,
retry scheduling, dead-letter handling, and stale receipt rejection.

They do NOT prove real MongoDB transaction or provider behavior.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure
import pytest

from app.repositories import (
    cafe_booking_recipient_delivery_repository as delivery_repo,
    cafe_booking_recipient_claim_repository as claim_repo,
    cafe_booking_recipient_receipt_repository as receipt_repo,
)


R = "507f1f77bcf86cd799439099"
C = "507f1f77bcf86cd799439011"
P = "507f1f77bcf86cd799439012"
EVENT = f"{R}:booking_confirmed"
CUSTOMER_KEY = f"{EVENT}:customer:{C}"
OWNER_KEY = f"{EVENT}:cafe_partner:{P}"
TOKEN = "a" * 64
NOW = datetime(2026, 10, 17, 15, tzinfo=timezone.utc)


def routing():
    return {
        "event_key": EVENT,
        "event_type": "booking_confirmed",
        "recipients": [
            {
                "recipient_type": "customer",
                "recipient_id": C,
                "delivery_key": CUSTOMER_KEY,
            },
            {
                "recipient_type": "cafe_partner",
                "recipient_id": P,
                "delivery_key": OWNER_KEY,
            },
        ],
    }


@pytest.fixture
def context(monkeypatch):
    collection = Mock()
    records = {}

    def insert(query, mutation, upsert=False):
        assert upsert is True
        assert set(mutation) == {"$setOnInsert"}
        key = query["_id"]
        created = key not in records
        if created:
            records[key] = dict(mutation["$setOnInsert"])
        return SimpleNamespace(
            acknowledged=True,
            matched_count=0 if created else 1,
            upserted_id=key if created else None,
        )

    def read(query, projection):
        return records.get(query["_id"])

    def claim(query, mutation, **kwargs):
        return {
            "_id": query["_id"],
            "event_key": query["event_key"],
            "event_type": query["event_type"],
            "recipient_type": query["recipient_type"],
            "recipient_id": query["recipient_id"],
            "delivery_state": "processing",
            "lease_token": mutation["$set"]["lease_token"],
            "locked_until": mutation["$set"]["locked_until"],
            "attempt_count": 1,
        }

    collection.update_one.side_effect = insert
    collection.find_one.side_effect = read
    collection.find_one_and_update.side_effect = claim

    mongo = SimpleNamespace(
        db=SimpleNamespace(
            cafe_booking_recipient_deliveries=collection,
        )
    )
    monkeypatch.setattr(delivery_repo, "mongo", mongo)
    monkeypatch.setattr(claim_repo, "mongo", mongo)
    monkeypatch.setattr(receipt_repo, "mongo", mongo)

    return SimpleNamespace(
        collection=collection,
        records=records,
    )


def initialize(context, values=None):
    return delivery_repo.ensure_cafe_booking_recipient_deliveries(
        routing=routing() if values is None else values,
        now=NOW,
    )


def claim(context, key=CUSTOMER_KEY, **changes):
    values = {
        "delivery_key": key,
        "now": NOW,
    }
    values.update(changes)
    return claim_repo.claim_cafe_booking_recipient_delivery(
        **values
    )


def receipt(context, key=CUSTOMER_KEY, **changes):
    context.collection.update_one.side_effect = None
    context.collection.update_one.return_value = SimpleNamespace(
        matched_count=1,
        modified_count=1,
    )
    values = {
        "delivery_key": key,
        "lease_token": TOKEN,
        "attempt_count": 1,
        "now": NOW,
    }
    values.update(changes)
    return receipt_repo.acknowledge_cafe_booking_recipient_delivery(
        **values
    )


def failure(context, key=CUSTOMER_KEY, **changes):
    context.collection.update_one.side_effect = None
    context.collection.update_one.return_value = SimpleNamespace(
        matched_count=1,
        modified_count=1,
    )
    values = {
        "delivery_key": key,
        "lease_token": TOKEN,
        "attempt_count": 1,
        "failure_code": "PROVIDER_UNAVAILABLE",
        "now": NOW,
    }
    values.update(changes)
    return receipt_repo.record_cafe_booking_recipient_failure(
        **values
    )


def test_two_independent_records_initialized(context):
    result = initialize(context)
    assert len(result["recipients"]) == 2
    assert result["all_delivered"] is False
    assert set(context.records) == {CUSTOMER_KEY, OWNER_KEY}
    assert context.records[CUSTOMER_KEY][
        "recipient_id"
    ] == ObjectId(C)
    assert context.records[OWNER_KEY][
        "recipient_id"
    ] == ObjectId(P)


def test_reinitialization_preserves_delivered_recipient(context):
    initialize(context)
    context.records[OWNER_KEY]["delivery_state"] = "delivered"

    result = initialize(context)
    assert result["all_delivered"] is False
    assert context.records[OWNER_KEY][
        "delivery_state"
    ] == "delivered"
    assert context.records[CUSTOMER_KEY][
        "delivery_state"
    ] == "pending"


def test_all_delivered_requires_both_recipients(context):
    initialize(context)
    context.records[OWNER_KEY]["delivery_state"] = "delivered"
    assert initialize(context)["all_delivered"] is False

    context.records[CUSTOMER_KEY]["delivery_state"] = "delivered"
    assert initialize(context)["all_delivered"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"recipients": []},
        {"recipients": [
            routing()["recipients"][0]
        ]},
        {"recipients": [
            routing()["recipients"][0],
            routing()["recipients"][0],
        ]},
        {"event_key": "invalid"},
    ],
)
def test_bad_audiences_rejected_before_write(context, change):
    values = {**routing(), **change}
    with pytest.raises(
        delivery_repo.CafeRecipientDeliveryError
    ):
        initialize(context, values=values)
    context.collection.update_one.assert_not_called()


def test_duplicate_insert_race_validated_against_stored_record(
    context,
):
    initialize(context)
    context.collection.update_one.side_effect = DuplicateKeyError(
        "already initialized"
    )
    result = initialize(context)
    assert len(result["recipients"]) == 2


def test_duplicate_race_with_missing_record_rejected(context):
    context.collection.update_one.side_effect = DuplicateKeyError(
        "duplicate key"
    )
    with pytest.raises(
        delivery_repo.CafeRecipientDeliveryError
    ):
        initialize(context)


def test_atomic_claim_targets_exact_recipient(context):
    claimed = claim(context)
    assert claimed["delivery_key"] == CUSTOMER_KEY
    assert claimed["recipient_id"] == C
    assert len(claimed["lease_token"]) == 64

    args = context.collection.find_one_and_update.call_args
    query, mutation = args.args
    assert query["_id"] == CUSTOMER_KEY
    assert query["recipient_id"] == ObjectId(C)
    assert query["event_key"] == EVENT
    assert mutation["$inc"] == {"attempt_count": 1}


def test_claims_receive_distinct_tokens(context):
    customer = claim(context)
    owner = claim(context, key=OWNER_KEY)
    assert customer["delivery_key"] != owner["delivery_key"]
    assert customer["lease_token"] != owner["lease_token"]


def test_claim_empty_returns_none(context):
    context.collection.find_one_and_update.side_effect = None
    context.collection.find_one_and_update.return_value = None
    assert claim(context) is None


@pytest.mark.parametrize(
    "seconds", [0, 29, 901, True, "90"]
)
def test_invalid_claim_duration_rejected(context, seconds):
    with pytest.raises(claim_repo.CafeRecipientClaimError):
        claim(context, lease_seconds=seconds)
    context.collection.find_one_and_update.assert_not_called()


@pytest.mark.parametrize(
    "key",
    [
        "invalid",
        f"{EVENT}:unknown:{C}",
        f"{R}:booking_approved:customer:{C}",
    ],
)
def test_forged_delivery_claim_rejected(context, key):
    with pytest.raises(claim_repo.CafeRecipientClaimError):
        claim(context, key=key)
    context.collection.find_one_and_update.assert_not_called()


def test_customer_receipt_cannot_modify_owner(context):
    result = receipt(context)
    assert result["delivery_state"] == "delivered"

    query, update = context.collection.update_one.call_args.args
    assert query["_id"] == CUSTOMER_KEY
    assert query["recipient_type"] == "customer"
    assert query["recipient_id"] == ObjectId(C)
    assert query["lease_token"] == TOKEN
    assert query["locked_until"] == {"$gt": NOW}
    assert update["$set"]["delivery_state"] == "delivered"


@pytest.mark.parametrize("attempt", range(1, 8))
def test_recipient_retry_backoff(context, attempt):
    result = failure(context, attempt_count=attempt)
    delay = min(30 * (2 ** (attempt - 1)), 3600)
    assert result["delivery_state"] == "pending"
    assert result["next_attempt_at"] == (
        NOW + timedelta(seconds=delay)
    )
    query = context.collection.update_one.call_args.args[0]
    assert query["attempt_count"] == attempt


@pytest.mark.parametrize("attempt", [8, 9])
def test_recipient_dead_letter(context, attempt):
    result = failure(context, attempt_count=attempt)
    assert result["delivery_state"] == "dead_letter"
    assert result["next_attempt_at"] is None
    update = context.collection.update_one.call_args.args[1]
    assert update["$set"]["dead_lettered_at"] == NOW


@pytest.mark.parametrize(
    "changes",
    [
        {"lease_token": "invalid"},
        {"attempt_count": 0},
        {"attempt_count": True},
        {"now": datetime(2026, 10, 17)},
        {"delivery_key": "invalid"},
    ],
)
def test_invalid_recipient_receipts_rejected(context, changes):
    with pytest.raises(receipt_repo.CafeRecipientReceiptError):
        receipt(context, **changes)
    context.collection.update_one.assert_not_called()


@pytest.mark.parametrize(
    "code", ["", "raw sensitive provider response", "x" * 100]
)
def test_sensitive_failure_messages_rejected(context, code):
    with pytest.raises(receipt_repo.CafeRecipientReceiptError):
        failure(context, failure_code=code)
    context.collection.update_one.assert_not_called()


@pytest.mark.parametrize("action", ["success", "failure"])
def test_stale_recipient_lease_cannot_update(context, action):
    context.collection.update_one.side_effect = None
    context.collection.update_one.return_value = SimpleNamespace(
        matched_count=0,
        modified_count=0,
    )
    values = {
        "delivery_key": CUSTOMER_KEY,
        "lease_token": TOKEN,
        "attempt_count": 1,
        "now": NOW,
    }

    if action == "success":
        function = (
            receipt_repo.acknowledge_cafe_booking_recipient_delivery
        )
    else:
        function = (
            receipt_repo.record_cafe_booking_recipient_failure
        )
        values["failure_code"] = "PROVIDER_UNAVAILABLE"

    with pytest.raises(
        receipt_repo.CafeRecipientReceiptConflict
    ):
        function(**values)


def test_database_claim_failure_propagates(context):
    context.collection.find_one_and_update.side_effect = (
        OperationFailure("database unavailable")
    )
    with pytest.raises(OperationFailure):
        claim(context)


def test_database_receipt_failure_propagates(context):
    context.collection.update_one.side_effect = None
    context.collection.update_one.side_effect = (
        OperationFailure("database unavailable")
    )
    with pytest.raises(OperationFailure):
        receipt_repo.acknowledge_cafe_booking_recipient_delivery(
            delivery_key=CUSTOMER_KEY,
            lease_token=TOKEN,
            attempt_count=1,
            now=NOW,
        )
