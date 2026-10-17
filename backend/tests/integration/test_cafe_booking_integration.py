"""Permanent internal booking orchestration contracts.

These tests verify component composition with mocks. They do not
establish real MongoDB concurrency, durable notifications, operating
hours, or production-grade user authorization.
"""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.schemas.reservation import (
    ReservationValidationError,
    validate_reservation_request,
)
from app.services import booking_service as svc


CUSTOMER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
KEY = "booking-request-000001"


def request():
    return {
        "venue_id": VENUE,
        "start_at": "2026-12-01T19:00:00+05:30",
        "party_size": 4,
        "idempotency_key": KEY,
        "notes": "Window seat",
    }


def policy(venue, cleaned, session):
    assert venue == VENUE
    assert cleaned["venue_id"] == VENUE
    assert session.in_transaction is True
    return {
        "venue_id": VENUE,
        "booking_allowed": True,
        "seat_capacity": 10,
        "duration_minutes": 60,
        "buffer_minutes": 15,
        "max_concurrent_bookings": 5,
    }


@pytest.fixture
def context(monkeypatch):
    events = []
    session = SimpleNamespace(in_transaction=True)
    booking_id = str(ObjectId())

    def transaction(*, operation):
        events.append("transaction")
        return operation(session)

    def fence(**kwargs):
        events.append("fence")
        assert kwargs["session"] is session
        return {"fence_version": 1}

    def existing(**kwargs):
        events.append("idempotency")
        assert kwargs["session"] is session
        return None

    def overlaps(**kwargs):
        events.append("overlaps")
        assert kwargs["session"] is session
        return []

    def capacity(**kwargs):
        events.append("capacity")
        assert kwargs["seat_capacity"] == 10
        assert kwargs["party_size"] == 4
        return {"available": True}

    def insert(**kwargs):
        events.append("insert")
        assert kwargs["session"] is session
        assert kwargs["request_data"]["start_at"] == request()["start_at"]
        return {
            "reservation_id": booking_id,
            "status": "pending",
            "venue_id": VENUE,
        }

    def outbox(**kwargs):
        events.append("outbox")
        assert kwargs["session"] is session
        assert kwargs["reservation_id"] == booking_id
        assert kwargs["venue_id"] == VENUE
        assert kwargs["customer_id"] == CUSTOMER
        assert kwargs["event_type"] == "booking_created"
        assert kwargs["reservation_status"] == "pending"
        return {
            "event_id": str(ObjectId()),
            "event_type": "booking_created",
            "delivery_state": "pending",
        }

    funcs = {
        "record_cafe_booking_event": outbox,
        "run_cafe_reservation_transaction": transaction,
        "acquire_venue_reservation_fence": fence,
        "find_reservation_by_idempotency_key": existing,
        "find_overlapping_active_reservations": overlaps,
        "evaluate_reservation_capacity": capacity,
        "insert_pending_cafe_reservation": insert,
    }

    mocks = {}
    for name, implementation in funcs.items():
        mocked = Mock(side_effect=implementation)
        monkeypatch.setattr(svc, name, mocked)
        mocks[name] = mocked

    return SimpleNamespace(
        events=events,
        session=session,
        booking_id=booking_id,
        mocks=mocks,
    )


def authorize(context):
    def callback(customer, venue, session):
        context.events.append("authorize")
        assert session is context.session
        return customer == CUSTOMER and venue == VENUE
    return callback


def booking(context, **changes):
    values = {
        "customer_id": CUSTOMER,
        "request_data": request(),
        "authorize_customer": authorize(context),
        "load_booking_policy": policy,
    }
    values.update(changes)
    return svc.create_pending_cafe_booking(**values)


def test_complete_pending_booking_flow(context):
    result = booking(context)
    assert result["reservation_id"] == context.booking_id
    assert result["status"] == "pending"
    assert result["replayed"] is False
    assert context.events == [
        "transaction",
        "authorize",
        "fence",
        "idempotency",
        "overlaps",
        "capacity",
        "insert",
        "outbox",
    ]


def test_request_not_modified(context):
    data = request()
    original = deepcopy(data)
    booking(context, request_data=data)
    assert data == original


def test_transaction_session_shared_across_components(context):
    booking(context)
    for name in (
        "acquire_venue_reservation_fence",
        "find_reservation_by_idempotency_key",
        "find_overlapping_active_reservations",
        "insert_pending_cafe_reservation",
        "record_cafe_booking_event",
    ):
        assert context.mocks[name].call_args.kwargs["session"] is context.session


@pytest.mark.parametrize(
    "customer",
    [None, "", "invalid", 123, True, "x" * 25],
)
def test_invalid_customer_rejected_before_transaction(context, customer):
    with pytest.raises(svc.CafeBookingError):
        booking(context, customer_id=customer)
    assert context.events == []


@pytest.mark.parametrize(
    "name",
    ["authorize_customer", "load_booking_policy"],
)
def test_missing_trusted_dependencies_fail_before_transaction(context, name):
    with pytest.raises(svc.CafeBookingError):
        booking(context, **{name: None})
    assert context.events == []


def test_unauthorized_customer_prevents_any_venue_write(context):
    with pytest.raises(svc.CafeBookingError):
        booking(context, authorize_customer=lambda *args: False)
    assert context.events == ["transaction"]
    context.mocks["acquire_venue_reservation_fence"].assert_not_called()


def test_nonboolean_authorization_fails_closed(context):
    with pytest.raises(svc.CafeBookingError):
        booking(context, authorize_customer=lambda *args: 1)
    context.mocks["acquire_venue_reservation_fence"].assert_not_called()


def test_unbookable_venue_cannot_insert(context):
    def closed(venue, cleaned, session):
        return {**policy(venue, cleaned, session), "booking_allowed": False}

    with pytest.raises(svc.CafeBookingError):
        booking(context, load_booking_policy=closed)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"seat_capacity": 0},
        {"seat_capacity": True},
        {"duration_minutes": 0},
        {"buffer_minutes": -1},
        {"max_concurrent_bookings": 0},
        {"venue_id": str(ObjectId())},
        {"booking_allowed": 1},
    ],
)
def test_invalid_trusted_policies_fail_closed(context, changes):
    def bad(venue, cleaned, session):
        return {**policy(venue, cleaned, session), **changes}

    with pytest.raises(svc.CafeBookingError):
        booking(context, load_booking_policy=bad)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


def test_incomplete_policy_fails_closed(context):
    def incomplete(venue, cleaned, session):
        result = policy(venue, cleaned, session)
        del result["seat_capacity"]
        return result

    with pytest.raises(svc.CafeBookingError):
        booking(context, load_booking_policy=incomplete)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


def test_overcapacity_cannot_insert(context):
    context.mocks["evaluate_reservation_capacity"].return_value = None
    context.mocks["evaluate_reservation_capacity"].side_effect = (
        lambda **kwargs: {"available": False}
    )
    with pytest.raises(svc.CafeBookingCapacityConflict):
        booking(context)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


def test_missing_capacity_acknowledgement_fails_closed(context):
    context.mocks["evaluate_reservation_capacity"].side_effect = (
        lambda **kwargs: {}
    )
    with pytest.raises(svc.CafeBookingCapacityConflict):
        booking(context)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


def test_exact_idempotency_replay_skips_capacity_and_insert(context):
    cleaned = validate_reservation_request(request())
    start = cleaned["start_at"]
    existing = {
        "_id": ObjectId(),
        "venue_id": ObjectId(VENUE),
        "customer_id": ObjectId(CUSTOMER),
        "idempotency_key": KEY,
        "status": "confirmed",
        "start_at": start,
        "end_at": start + timedelta(minutes=75),
        "party_size": 4,
        "duration_minutes": 60,
        "buffer_minutes": 15,
        "notes": "Window seat",
    }
    context.mocks["find_reservation_by_idempotency_key"].side_effect = (
        lambda **kwargs: existing
    )
    result = booking(context)
    assert result["replayed"] is True
    assert result["status"] == "confirmed"
    context.mocks["find_overlapping_active_reservations"].assert_not_called()
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()
    context.mocks["record_cafe_booking_event"].assert_not_called()


def test_mismatched_replay_rejected_without_insert(context):
    cleaned = validate_reservation_request(request())
    start = cleaned["start_at"]
    existing = {
        "_id": ObjectId(),
        "venue_id": ObjectId(VENUE),
        "customer_id": ObjectId(),
        "idempotency_key": KEY,
        "status": "pending",
        "start_at": start,
        "end_at": start + timedelta(minutes=75),
        "party_size": 4,
        "duration_minutes": 60,
        "buffer_minutes": 15,
        "notes": "Window seat",
    }
    context.mocks["find_reservation_by_idempotency_key"].side_effect = (
        lambda **kwargs: existing
    )
    with pytest.raises(ValueError):
        booking(context)
    context.mocks["insert_pending_cafe_reservation"].assert_not_called()


def test_database_failure_propagates(context):
    context.mocks["insert_pending_cafe_reservation"].side_effect = (
        OperationFailure("simulated write failure")
    )
    with pytest.raises(OperationFailure):
        booking(context)


def test_bad_insert_acknowledgement_not_success(context):
    context.mocks["insert_pending_cafe_reservation"].side_effect = (
        lambda **kwargs: {
            "reservation_id": "bad",
            "status": "pending",
        }
    )
    with pytest.raises(svc.CafeBookingError):
        booking(context)


def test_invalid_request_fails_before_transaction(context):
    data = {**request(), "status": "confirmed"}
    with pytest.raises(ReservationValidationError):
        booking(context, request_data=data)
    assert context.events == []


def test_module_has_no_public_booking_route():
    assert callable(svc.create_pending_cafe_booking)
    assert not hasattr(svc, "booking_blueprint")
    assert not hasattr(svc, "create_booking_route")


def test_outbox_failure_aborts_booking_operation(context):
    from pymongo.errors import OperationFailure

    context.mocks["record_cafe_booking_event"].side_effect = (
        OperationFailure("outbox insert failed")
    )

    with pytest.raises(OperationFailure):
        booking(context)

    context.mocks["insert_pending_cafe_reservation"].assert_called_once()
    context.mocks["record_cafe_booking_event"].assert_called_once()

    # Mock side_effect raises before the outbox function body runs.
    # Its invocation is verified above with assert_called_once().
    assert context.events[-1] == "insert"


def test_success_emits_one_outbox_event(context):
    result = booking(context)

    assert result["replayed"] is False
    context.mocks["record_cafe_booking_event"].assert_called_once()

    arguments = context.mocks[
        "record_cafe_booking_event"
    ].call_args.kwargs

    assert arguments["reservation_id"] == result["reservation_id"]
    assert arguments["event_type"] == "booking_created"
    assert arguments["session"] is context.session
