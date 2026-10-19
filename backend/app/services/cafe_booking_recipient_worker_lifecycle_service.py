"""Bounded, explicitly invoked cafe recipient worker lifecycle.

No automatic scheduler, HTTP route or provider is activated here.
"""

from app.services.cafe_booking_recipient_exhausted_maintenance_service import (
    maintain_exhausted_cafe_booking_recipients,
)
from app.services.cafe_booking_recipient_worker_runner_service import (
    run_bounded_cafe_booking_recipient_worker,
)
from app.services.cafe_booking_recipient_provider_idempotency_service import (
    make_cafe_booking_idempotent_adapter,
)


class CafeRecipientLifecycleError(ValueError):
    """Invalid lifecycle configuration or result."""


def run_cafe_booking_recipient_lifecycle(
    *,
    enabled=False,
    maintenance_enabled=False,
    delivery_enabled=False,
    provider=None,
    max_maintenance_records=10,
    max_delivery_events=1,
):
    """Run at most one explicitly requested, bounded lifecycle cycle."""
    disabled = {
        "outcome": "disabled",
        "maintenance": None,
        "delivery": None,
    }

    if enabled is not True:
        return disabled

    if maintenance_enabled is not True and delivery_enabled is not True:
        return disabled

    if maintenance_enabled is True and (
        type(max_maintenance_records) is not int
        or not 1 <= max_maintenance_records <= 100
    ):
        raise CafeRecipientLifecycleError("Invalid maintenance limit.")

    if delivery_enabled is True:
        if (
            type(max_delivery_events) is not int
            or not 1 <= max_delivery_events <= 20
        ):
            raise CafeRecipientLifecycleError("Invalid delivery limit.")

        # Validate before maintenance to avoid partial work with
        # a provider that cannot satisfy the idempotency contract.
        make_cafe_booking_idempotent_adapter(provider)

    maintenance = None
    delivery = None

    if maintenance_enabled is True:
        maintenance = maintain_exhausted_cafe_booking_recipients(
            enabled=True,
            max_records=max_maintenance_records,
        )
        if (
            not isinstance(maintenance, dict)
            or maintenance.get("outcome") != "completed"
            or type(maintenance.get("examined_count")) is not int
            or type(maintenance.get("reconciled_count")) is not int
            or not 0 <= maintenance["reconciled_count"]
            <= maintenance["examined_count"] <= max_maintenance_records
        ):
            raise CafeRecipientLifecycleError(
                "Invalid maintenance outcome; delivery withheld."
            )

    if delivery_enabled is True:
        delivery = run_bounded_cafe_booking_recipient_worker(
            provider=provider,
            enabled=True,
            max_events=max_delivery_events,
        )
        if (
            not isinstance(delivery, dict)
            or delivery.get("outcome") not in {
                "empty", "limit_reached", "incomplete", "blocked"
            }
            or type(delivery.get("processed_count")) is not int
            or type(delivery.get("delivered_count")) is not int
            or not 0 <= delivery["delivered_count"]
            <= delivery["processed_count"] <= max_delivery_events
        ):
            raise CafeRecipientLifecycleError(
                "Invalid delivery worker outcome."
            )

    return {
        "outcome": "completed",
        "maintenance": maintenance,
        "delivery": delivery,
    }
