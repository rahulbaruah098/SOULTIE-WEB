from unittest.mock import patch

import pytest

from app.services import cafe_booking_recipient_worker_runner_service as runner
from app.services.cafe_booking_recipient_provider_idempotency_service import (
    CafeProviderIdempotencyError,
)


class SafeFakeProvider:
    supports_durable_idempotency = True

    def send_once(self, **kwargs):
        raise AssertionError("No external sends in runner tests")


def _result(outcome):
    return {
        "outcome": outcome,
        "all_delivered": outcome == "delivered",
    }


def test_disabled_runner_does_not_claim():
    with patch.object(
        runner, "process_one_cafe_booking_outbox_event_if_enabled"
    ) as execute:
        result = runner.run_bounded_cafe_booking_recipient_worker(
            provider=object()
        )
        assert result["outcome"] == "disabled"
        assert result["processed_count"] == 0
        execute.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 21, True, 1.5, "2"])
def test_invalid_batch_size_fails_before_claim(limit):
    with patch.object(
        runner, "process_one_cafe_booking_outbox_event_if_enabled"
    ) as execute:
        with pytest.raises(runner.CafeRecipientRunnerError):
            runner.run_bounded_cafe_booking_recipient_worker(
                provider=SafeFakeProvider(),
                enabled=True,
                max_events=limit,
            )
        execute.assert_not_called()


def test_unsafe_provider_fails_before_claim():
    with patch.object(
        runner, "process_one_cafe_booking_outbox_event_if_enabled"
    ) as execute:
        with pytest.raises(CafeProviderIdempotencyError):
            runner.run_bounded_cafe_booking_recipient_worker(
                provider=object(),
                enabled=True,
            )
        execute.assert_not_called()


def test_delivered_events_stop_at_batch_limit():
    with patch.object(
        runner,
        "process_one_cafe_booking_outbox_event_if_enabled",
        return_value=_result("delivered"),
    ) as execute:
        result = runner.run_bounded_cafe_booking_recipient_worker(
            provider=SafeFakeProvider(),
            enabled=True,
            max_events=3,
        )

    assert result == {
        "outcome": "limit_reached",
        "processed_count": 3,
        "delivered_count": 3,
    }
    assert execute.call_count == 3


@pytest.mark.parametrize("stop", ["incomplete", "blocked"])
def test_failure_or_blockage_stops_immediately(stop):
    with patch.object(
        runner,
        "process_one_cafe_booking_outbox_event_if_enabled",
        side_effect=[_result("delivered"), _result(stop)],
    ) as execute:
        result = runner.run_bounded_cafe_booking_recipient_worker(
            provider=SafeFakeProvider(),
            enabled=True,
            max_events=10,
        )

    assert result == {
        "outcome": stop,
        "processed_count": 2,
        "delivered_count": 1,
    }
    assert execute.call_count == 2


def test_empty_queue_stops_without_counting_event():
    with patch.object(
        runner,
        "process_one_cafe_booking_outbox_event_if_enabled",
        return_value={"outcome": "empty"},
    ) as execute:
        result = runner.run_bounded_cafe_booking_recipient_worker(
            provider=SafeFakeProvider(),
            enabled=True,
            max_events=10,
        )

    assert result["outcome"] == "empty"
    assert result["processed_count"] == 0
    assert execute.call_count == 1


def test_invalid_worker_outcome_fails_closed():
    with patch.object(
        runner,
        "process_one_cafe_booking_outbox_event_if_enabled",
        return_value={"outcome": "unexpected"},
    ):
        with pytest.raises(runner.CafeRecipientRunnerError):
            runner.run_bounded_cafe_booking_recipient_worker(
                provider=SafeFakeProvider(),
                enabled=True,
            )
