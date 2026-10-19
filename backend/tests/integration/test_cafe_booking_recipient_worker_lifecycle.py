"""Fail-closed operational lifecycle acceptance tests."""

from unittest.mock import patch

import pytest

from app.services import (
    cafe_booking_recipient_worker_lifecycle_service as lifecycle,
)
from app.services.cafe_booking_recipient_provider_idempotency_service import (
    CafeProviderIdempotencyError,
)


class FakeProvider:
    supports_durable_idempotency = True

    def send_once(self, **kwargs):
        raise AssertionError("No real delivery expected")


MAINTENANCE = {
    "outcome": "completed",
    "examined_count": 2,
    "reconciled_count": 1,
}

DELIVERY = {
    "outcome": "empty",
    "processed_count": 0,
    "delivered_count": 0,
}


def test_default_disabled_never_runs_operations():
    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients"
    ) as maintain, patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker"
    ) as deliver:
        result = lifecycle.run_cafe_booking_recipient_lifecycle(
            provider=object()
        )
        assert result == {
            "outcome": "disabled",
            "maintenance": None,
            "delivery": None,
        }
        maintain.assert_not_called()
        deliver.assert_not_called()


@pytest.mark.parametrize("value", [False, None, 0, 1, "true"])
def test_nonliteral_activation_does_nothing(value):
    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients"
    ) as maintain:
        result = lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=value,
            maintenance_enabled=True,
        )
        assert result["outcome"] == "disabled"
        maintain.assert_not_called()


def test_maintenance_only_does_not_need_provider():
    with patch.object(
        lifecycle,
        "maintain_exhausted_cafe_booking_recipients",
        return_value=MAINTENANCE,
    ) as maintain, patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker"
    ) as deliver:
        result = lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=True,
            maintenance_enabled=True,
            max_maintenance_records=3,
        )
        assert result["maintenance"] == MAINTENANCE
        assert result["delivery"] is None
        maintain.assert_called_once_with(
            enabled=True, max_records=3
        )
        deliver.assert_not_called()


def test_delivery_only_does_not_run_maintenance():
    with patch.object(
        lifecycle,
        "run_bounded_cafe_booking_recipient_worker",
        return_value=DELIVERY,
    ) as deliver, patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients"
    ) as maintain:
        result = lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=True,
            delivery_enabled=True,
            provider=FakeProvider(),
        )
        assert result["delivery"] == DELIVERY
        deliver.assert_called_once()
        maintain.assert_not_called()


def test_both_operations_execute_maintenance_first():
    sequence = []

    def maintain(**kwargs):
        sequence.append("maintenance")
        return MAINTENANCE

    def deliver(**kwargs):
        sequence.append("delivery")
        return DELIVERY

    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients",
        side_effect=maintain,
    ), patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker",
        side_effect=deliver,
    ):
        result = lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=True,
            maintenance_enabled=True,
            delivery_enabled=True,
            provider=FakeProvider(),
        )

    assert sequence == ["maintenance", "delivery"]
    assert result["outcome"] == "completed"


def test_unsafe_provider_refused_before_maintenance():
    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients"
    ) as maintain:
        with pytest.raises(CafeProviderIdempotencyError):
            lifecycle.run_cafe_booking_recipient_lifecycle(
                enabled=True,
                maintenance_enabled=True,
                delivery_enabled=True,
                provider=object(),
            )
        maintain.assert_not_called()


@pytest.mark.parametrize("value", [0, 101, True, "10"])
def test_invalid_maintenance_limit(value):
    with pytest.raises(lifecycle.CafeRecipientLifecycleError):
        lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=True,
            maintenance_enabled=True,
            max_maintenance_records=value,
        )


@pytest.mark.parametrize("value", [0, 21, True, "2"])
def test_invalid_delivery_limit(value):
    with pytest.raises(lifecycle.CafeRecipientLifecycleError):
        lifecycle.run_cafe_booking_recipient_lifecycle(
            enabled=True,
            delivery_enabled=True,
            provider=FakeProvider(),
            max_delivery_events=value,
        )


def test_maintenance_failure_prevents_delivery():
    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients",
        side_effect=RuntimeError("database unavailable"),
    ), patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker"
    ) as deliver:
        with pytest.raises(RuntimeError, match="database unavailable"):
            lifecycle.run_cafe_booking_recipient_lifecycle(
                enabled=True,
                maintenance_enabled=True,
                delivery_enabled=True,
                provider=FakeProvider(),
            )
        deliver.assert_not_called()


def test_inconsistent_maintenance_result_blocks_delivery():
    with patch.object(
        lifecycle, "maintain_exhausted_cafe_booking_recipients",
        return_value={
            "outcome": "completed",
            "examined_count": 1,
            "reconciled_count": 2,
        },
    ), patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker"
    ) as deliver:
        with pytest.raises(lifecycle.CafeRecipientLifecycleError):
            lifecycle.run_cafe_booking_recipient_lifecycle(
                enabled=True,
                maintenance_enabled=True,
                delivery_enabled=True,
                provider=FakeProvider(),
            )
        deliver.assert_not_called()


def test_invalid_delivery_result_rejected():
    with patch.object(
        lifecycle, "run_bounded_cafe_booking_recipient_worker",
        return_value={"outcome": "unexpected"},
    ):
        with pytest.raises(lifecycle.CafeRecipientLifecycleError):
            lifecycle.run_cafe_booking_recipient_lifecycle(
                enabled=True,
                delivery_enabled=True,
                provider=FakeProvider(),
            )
