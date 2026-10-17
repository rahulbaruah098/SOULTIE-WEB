"""Permanent mocked regression for cafe booking failure reconciliation.

These tests do not open database connections or send notifications.
A recovered outcome must be supported by a verified persisted booking.
An unresolved outcome must never cause another booking creation.
"""

from unittest.mock import patch

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.services import booking_service
from app.services.cafe_booking_outcome_reconciliation_service import (
    CafeBookingOutcomeConflict,
    CafeBookingOutcomeUnavailable,
)


CUSTOMER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"

REQUEST = {
    "venue_id": VENUE,
    "start_at": "2026-12-02T19:00:00+05:30",
    "party_size": 4,
    "idempotency_key": "booking-request-000001",
    "notes": "Window seat",
}

EXISTING = {
    "reservation_id": "507f1f77bcf86cd799439099",
    "venue_id": VENUE,
    "status": "pending",
    "replayed": True,
}


def _create():
    return booking_service.create_pending_cafe_booking(
        customer_id=CUSTOMER,
        request_data=dict(REQUEST),
        authorize_customer=lambda *args: True,
        load_booking_policy=lambda *args: {},
    )


class SimulatedUnknownCommit(OperationFailure):
    """Simulate PyMongo's public error-label contract."""

    def has_error_label(self, label):
        return label == "UnknownTransactionCommitResult"


def test_duplicate_key_returns_only_verified_existing_booking():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=DuplicateKeyError("duplicate"),
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
        return_value=dict(EXISTING),
    ) as reconcile:
        result = _create()

        assert result == EXISTING
        transaction.assert_called_once()
        reconcile.assert_called_once_with(
            customer_id=CUSTOMER,
            request_data=REQUEST,
        )


def test_duplicate_key_without_evidence_fails_closed():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=DuplicateKeyError("duplicate"),
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
        side_effect=CafeBookingOutcomeUnavailable("unknown"),
    ) as reconcile:
        with pytest.raises(CafeBookingOutcomeUnavailable):
            _create()

        transaction.assert_called_once()
        reconcile.assert_called_once()


def test_duplicate_key_for_different_intent_rejected():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=DuplicateKeyError("duplicate"),
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
        side_effect=CafeBookingOutcomeConflict("different"),
    ) as reconcile:
        with pytest.raises(CafeBookingOutcomeConflict):
            _create()

        transaction.assert_called_once()
        reconcile.assert_called_once()


def test_unknown_commit_returns_only_verified_existing_booking():
    error = SimulatedUnknownCommit("uncertain commit")

    assert error.has_error_label("UnknownTransactionCommitResult")

    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=error,
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
        return_value=dict(EXISTING),
    ) as reconcile:
        result = _create()

        assert result == EXISTING
        transaction.assert_called_once()
        reconcile.assert_called_once_with(
            customer_id=CUSTOMER,
            request_data=REQUEST,
        )


def test_unknown_commit_without_evidence_fails_closed():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=SimulatedUnknownCommit("uncertain"),
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
        side_effect=CafeBookingOutcomeUnavailable("unresolved"),
    ) as reconcile:
        with pytest.raises(CafeBookingOutcomeUnavailable):
            _create()

        transaction.assert_called_once()
        reconcile.assert_called_once()


def test_ordinary_database_error_is_not_reconciled():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        side_effect=OperationFailure("ordinary database error"),
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
    ) as reconcile:
        with pytest.raises(OperationFailure):
            _create()

        transaction.assert_called_once()
        reconcile.assert_not_called()


def test_successful_booking_does_not_run_reconciliation():
    inserted = {
        "reservation_id": "507f1f77bcf86cd799439099",
        "venue_id": VENUE,
        "status": "pending",
        "replayed": False,
    }

    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
        return_value=inserted,
    ) as transaction, patch.object(
        booking_service,
        "reconcile_existing_cafe_booking",
    ) as reconcile:
        assert _create() == inserted

        transaction.assert_called_once()
        reconcile.assert_not_called()


def test_invalid_customer_rejected_before_transaction():
    with patch.object(
        booking_service,
        "run_cafe_reservation_transaction",
    ) as transaction:
        with pytest.raises(booking_service.CafeBookingError):
            booking_service.create_pending_cafe_booking(
                customer_id="invalid",
                request_data=dict(REQUEST),
                authorize_customer=lambda *args: True,
                load_booking_policy=lambda *args: {},
            )

        transaction.assert_not_called()
