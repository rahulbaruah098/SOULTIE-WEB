"""Permanent mocked lifecycle service authorization tests.

These tests do not create MongoDB sessions or modify databases.
Authenticated identity must be supplied by the future HTTP layer.
"""

from unittest.mock import patch

import pytest

from app.services import cafe_reservation_lifecycle_service as svc


CUSTOMER = "507f1f77bcf86cd799439011"
PARTNER = "507f1f77bcf86cd799439012"
VENUE = "507f191e810c19729de860ea"
BOOKING = "507f1f77bcf86cd799439099"


class ActiveSession:
    in_transaction = True


class InactiveSession:
    in_transaction = False


def owner_context(*, partner_id, venue_id):
    assert partner_id == PARTNER
    assert venue_id == VENUE
    return {
        "account_type": "cafe_partner",
        "partner_id": PARTNER,
        "venue_id": VENUE,
    }


def mock_transaction(*, operation):
    return operation(ActiveSession())


def booking_result(**kwargs):
    return {
        "reservation_id": kwargs["reservation_id"],
        "previous_status": "pending",
        "status": kwargs["decision"],
    }


def owner_action(**overrides):
    data = {
        "reservation_id": BOOKING,
        "actor_id": PARTNER,
        "actor_role": "cafe_partner",
        "decision": "confirmed",
        "venue_id": VENUE,
    }
    data.update(overrides)
    return svc.change_cafe_reservation_status(**data)


def customer_action(**overrides):
    data = {
        "reservation_id": BOOKING,
        "actor_id": CUSTOMER,
        "actor_role": "customer",
        "decision": "cancelled",
    }
    data.update(overrides)
    return svc.change_cafe_reservation_status(**data)


@pytest.mark.parametrize(
    "decision",
    [
        "confirmed",
        "rejected",
        "cancelled",
        "completed",
        "no_show",
    ],
)
def test_authorized_owner_actions_use_transaction(
    decision,
):
    with patch.object(
        svc,
        "authorize_cafe_partner",
        side_effect=owner_context,
    ) as authorization, patch.object(
        svc,
        "run_cafe_reservation_transaction",
        side_effect=mock_transaction,
    ) as transaction, patch.object(
        svc,
        "transition_cafe_reservation_status",
        side_effect=booking_result,
    ) as persistence:

        result = owner_action(decision=decision)

        assert result["status"] == decision
        authorization.assert_called_once_with(
            partner_id=PARTNER,
            venue_id=VENUE,
        )
        transaction.assert_called_once()
        persistence.assert_called_once()

        arguments = persistence.call_args.kwargs
        assert arguments["actor_id"] == PARTNER
        assert arguments["actor_role"] == "cafe_partner"
        assert arguments["decision"] == decision
        assert arguments["session"].in_transaction is True


def test_customer_cancellation_uses_no_partner_authorization():
    with patch.object(
        svc,
        "authorize_cafe_partner",
    ) as authorization, patch.object(
        svc,
        "run_cafe_reservation_transaction",
        side_effect=mock_transaction,
    ) as transaction, patch.object(
        svc,
        "transition_cafe_reservation_status",
        side_effect=booking_result,
    ) as persistence:

        assert customer_action()["status"] == "cancelled"

        authorization.assert_not_called()
        transaction.assert_called_once()

        arguments = persistence.call_args.kwargs
        assert arguments["actor_id"] == CUSTOMER
        assert arguments["actor_role"] == "customer"


@pytest.mark.parametrize(
    "decision",
    ["confirmed", "rejected", "completed", "no_show"],
)
def test_customer_cannot_perform_partner_decisions(decision):
    with patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            customer_action(decision=decision)

        transaction.assert_not_called()


@pytest.mark.parametrize(
    "identity",
    ["", "not-an-object-id", None],
)
def test_invalid_customer_identity_rejected(identity):
    with patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            customer_action(actor_id=identity)

        transaction.assert_not_called()


def test_customer_cannot_supply_venue_identity():
    with patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            customer_action(venue_id=VENUE)

        transaction.assert_not_called()


def test_owner_must_supply_venue_identity():
    with patch.object(
        svc,
        "authorize_cafe_partner",
    ) as authorization, patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            owner_action(venue_id=None)

        authorization.assert_not_called()
        transaction.assert_not_called()


def test_owner_with_invalid_live_authorization_denied():
    with patch.object(
        svc,
        "authorize_cafe_partner",
        return_value=None,
    ), patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            owner_action()

        transaction.assert_not_called()


def test_owner_with_mismatched_authorization_denied():
    with patch.object(
        svc,
        "authorize_cafe_partner",
        return_value={
            "account_type": "cafe_partner",
            "partner_id": PARTNER,
            "venue_id": "507f191e810c19729de860eb",
        },
    ), patch.object(
        svc,
        "run_cafe_reservation_transaction",
    ) as transaction:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            owner_action()

        transaction.assert_not_called()


def test_inactive_transaction_never_reaches_repository():
    def inactive_transaction(*, operation):
        return operation(InactiveSession())

    with patch.object(
        svc,
        "run_cafe_reservation_transaction",
        side_effect=inactive_transaction,
    ), patch.object(
        svc,
        "transition_cafe_reservation_status",
    ) as persistence:

        with pytest.raises(
            svc.CafeReservationLifecycleServiceDenied
        ):
            customer_action()

        persistence.assert_not_called()


def test_repository_failure_propagates_from_transaction():
    class SimulatedConflict(ValueError):
        pass

    with patch.object(
        svc,
        "run_cafe_reservation_transaction",
        side_effect=mock_transaction,
    ) as transaction, patch.object(
        svc,
        "transition_cafe_reservation_status",
        side_effect=SimulatedConflict("CAS conflict"),
    ) as persistence:

        with pytest.raises(SimulatedConflict):
            customer_action()

        transaction.assert_called_once()
        persistence.assert_called_once()
