"""Focused tests for pure cafe recipient retry policy."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.cafe_booking_recipient_retry_policy_service import (
    CafeRecipientRetryPolicyError,
    MAX_ATTEMPTS,
    calculate_cafe_recipient_retry,
)


NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "attempt,seconds",
    [
        (1, 30),
        (2, 60),
        (3, 120),
        (4, 240),
        (5, 480),
        (6, 960),
        (7, 1920),
    ],
)
def test_retry_delay_is_bounded_and_exponential(attempt, seconds):
    result = calculate_cafe_recipient_retry(
        attempt_count=attempt, now=NOW
    )
    assert result["action"] == "retry"
    assert result["delay_seconds"] == seconds
    assert result["retry_after"] == NOW + timedelta(seconds=seconds)


def test_maximum_attempt_dead_letters_without_scheduling():
    result = calculate_cafe_recipient_retry(
        attempt_count=MAX_ATTEMPTS, now=NOW
    )
    assert result == {
        "action": "dead_letter",
        "attempt_count": MAX_ATTEMPTS,
        "retry_after": None,
        "delay_seconds": None,
    }


@pytest.mark.parametrize(
    "invalid",
    [None, False, True, 0, -1, 1.0, "1", {}, []],
)
def test_invalid_attempt_count_fails_closed(invalid):
    with pytest.raises(CafeRecipientRetryPolicyError):
        calculate_cafe_recipient_retry(
            attempt_count=invalid, now=NOW
        )


@pytest.mark.parametrize(
    "invalid",
    [None, "", 123, datetime(2026, 10, 18)],
)
def test_invalid_timestamp_fails_closed(invalid):
    with pytest.raises(CafeRecipientRetryPolicyError):
        calculate_cafe_recipient_retry(
            attempt_count=1, now=invalid
        )


def test_non_utc_input_normalized_to_utc():
    offset = timezone(timedelta(hours=5, minutes=30))
    local = NOW.astimezone(offset)

    result = calculate_cafe_recipient_retry(
        attempt_count=1, now=local
    )

    assert result["retry_after"] == NOW + timedelta(seconds=30)
    assert result["retry_after"].tzinfo == timezone.utc


def test_retry_policy_is_deterministic():
    first = calculate_cafe_recipient_retry(
        attempt_count=3, now=NOW
    )
    second = calculate_cafe_recipient_retry(
        attempt_count=3, now=NOW
    )
    assert first == second


@pytest.mark.parametrize("attempt", range(1, 9))
def test_repository_uses_shared_retry_policy(monkeypatch, attempt):
    from bson import ObjectId
    from app.repositories import (
        cafe_booking_recipient_receipt_repository as receipt,
    )

    captured = {}

    def fake_write(query, update):
        captured["query"] = query
        captured["update"] = update

    monkeypatch.setattr(receipt, "_write", fake_write)

    reservation_id = str(ObjectId())
    customer_id = str(ObjectId())

    key = (
        f"{reservation_id}:booking_created:"
        f"customer:{customer_id}"
    )

    actual = receipt.record_cafe_booking_recipient_failure(
        delivery_key=key,
        lease_token="a" * 64,
        attempt_count=attempt,
        failure_code="PROVIDER_UNAVAILABLE",
        now=NOW,
    )

    expected = calculate_cafe_recipient_retry(
        attempt_count=attempt,
        now=NOW,
    )

    fields = captured["update"]["$set"]

    assert fields["next_attempt_at"] == expected["retry_after"]
    assert actual["next_attempt_at"] == expected["retry_after"]

    state = (
        "dead_letter"
        if expected["action"] == "dead_letter"
        else "pending"
    )
    assert actual["delivery_state"] == state
    assert fields["delivery_state"] == state


def test_repository_retry_constants_match_shared_policy():
    from app.repositories import (
        cafe_booking_recipient_receipt_repository as receipt,
    )
    from app.services import (
        cafe_booking_recipient_retry_policy_service as policy,
    )

    assert receipt.MAX_RECIPIENT_ATTEMPTS == policy.MAX_ATTEMPTS
    assert receipt.RETRY_BASE_SECONDS == policy.BASE_DELAY_SECONDS
    assert receipt.RETRY_MAX_SECONDS == policy.MAX_DELAY_SECONDS


@pytest.mark.parametrize("attempt", [8, 9, 10, 100])
def test_attempts_at_or_above_limit_dead_letter(attempt):
    decision = calculate_cafe_recipient_retry(
        attempt_count=attempt,
        now=NOW,
    )

    assert decision == {
        "action": "dead_letter",
        "attempt_count": attempt,
        "retry_after": None,
        "delay_seconds": None,
    }


@pytest.mark.parametrize("attempt", [8, 9, 10])
def test_repository_dead_letters_all_over_limit_attempts(
    monkeypatch, attempt
):
    from bson import ObjectId
    from app.repositories import (
        cafe_booking_recipient_receipt_repository as receipt,
    )

    writes = []

    monkeypatch.setattr(
        receipt,
        "_write",
        lambda query, update: writes.append(update),
    )

    reservation = str(ObjectId())
    customer = str(ObjectId())

    result = receipt.record_cafe_booking_recipient_failure(
        delivery_key=(
            f"{reservation}:booking_confirmed:customer:{customer}"
        ),
        lease_token="a" * 64,
        attempt_count=attempt,
        failure_code="PROVIDER_UNAVAILABLE",
        now=NOW,
    )

    assert result["delivery_state"] == "dead_letter"
    assert result["next_attempt_at"] is None
    assert len(writes) == 1
    assert writes[0]["$set"]["delivery_state"] == "dead_letter"
    assert writes[0]["$set"]["next_attempt_at"] is None
