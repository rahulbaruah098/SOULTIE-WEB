"""Permanent validation and state-transition tests for cafe reservations."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.schemas.reservation import (
    ACTIVE_RESERVATION_STATUSES,
    RESERVATION_STATUSES,
    RESERVATION_TRANSITIONS,
    ReservationTransitionError,
    ReservationValidationError,
    validate_reservation_request,
    validate_reservation_transition,
)


VENUE = "507f191e810c19729de860ea"


def valid_request():
    return {
        "venue_id": VENUE,
        "start_at": "2026-12-01T19:00:00+05:30",
        "party_size": 4,
        "idempotency_key": "booking-request-000001",
        "notes": " Window seat ",
    }


def test_valid_reservation_request_normalizes_timestamp_and_notes():
    payload = valid_request()
    before = deepcopy(payload)

    result = validate_reservation_request(payload)

    assert result == {
        "venue_id": VENUE,
        "start_at": datetime(
            2026, 12, 1, 13, 30, tzinfo=timezone.utc
        ),
        "party_size": 4,
        "idempotency_key": "booking-request-000001",
        "notes": "Window seat",
    }
    assert payload == before
    assert result is not payload


def test_omitted_notes_become_empty():
    payload = valid_request()
    del payload["notes"]
    assert validate_reservation_request(payload)["notes"] == ""


def test_null_notes_become_empty():
    payload = valid_request()
    payload["notes"] = None
    assert validate_reservation_request(payload)["notes"] == ""


def test_party_size_boundaries():
    for party in (1, 100):
        payload = valid_request()
        payload["party_size"] = party
        assert validate_reservation_request(payload)["party_size"] == party


@pytest.mark.parametrize(
    "party",
    [
        None, True, False, 0, -1, 101,
        2.5, "4", [], {}, 999999,
    ],
)
def test_invalid_party_size_rejected(party):
    payload = valid_request()
    payload["party_size"] = party
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "venue",
    [
        None, "", "bad", 123, True,
        [], {}, "f" * 25,
    ],
)
def test_invalid_venue_identifier_rejected(venue):
    payload = valid_request()
    payload["venue_id"] = venue
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (
            "2026-12-01T13:30:00Z",
            datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-12-01T13:30:00+00:00",
            datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-12-01T19:00:00+05:30",
            datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-12-01T08:30:00-05:00",
            datetime(2026, 12, 1, 13, 30, tzinfo=timezone.utc),
        ),
    ],
)
def test_timezones_convert_to_same_utc_instant(
    timestamp, expected
):
    payload = valid_request()
    payload["start_at"] = timestamp
    assert validate_reservation_request(payload)["start_at"] == expected


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        123,
        "",
        "tomorrow evening",
        "2026-12-01T19:00:00",
        "not-a-date",
        "2026-13-01T19:00:00+05:30",
        "2026-12-01T25:00:00+05:30",
        "2026-12-01T19:00:01+05:30",
        "2026-12-01T19:00:00.000001+05:30",
    ],
)
def test_invalid_or_non_aligned_booking_time_rejected(timestamp):
    payload = valid_request()
    payload["start_at"] = timestamp
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "key",
    [
        "request01",
        "booking-request-000001",
        "12345678",
        "A" * 128,
        "booking_2026:12.01-01",
    ],
)
def test_valid_idempotency_keys(key):
    payload = valid_request()
    payload["idempotency_key"] = key
    assert validate_reservation_request(payload)["idempotency_key"] == key


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "short",
        "bad key with spaces",
        " leading-key",
        "-badstart123",
        "key/with/path",
        "key\nwithnewline",
        123,
        True,
        "A" * 129,
    ],
)
def test_invalid_idempotency_keys_rejected(key):
    payload = valid_request()
    payload["idempotency_key"] = key
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


def test_notes_maximum_length():
    payload = valid_request()
    payload["notes"] = "x" * 500
    assert len(
        validate_reservation_request(payload)["notes"]
    ) == 500


@pytest.mark.parametrize(
    "notes",
    [
        123,
        True,
        [],
        {},
        "x" * 501,
    ],
)
def test_invalid_notes_rejected(notes):
    payload = valid_request()
    payload["notes"] = notes
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "field",
    [
        "venue_id",
        "start_at",
        "party_size",
        "idempotency_key",
    ],
)
def test_required_reservation_fields(field):
    payload = valid_request()
    del payload[field]
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "owner_id",
        "approval_status",
        "active",
        "reviewed_by",
        "confirmed_at",
        "customer_id",
        "booking_id",
        "reservation_id",
        "payment_status",
        "created_at",
        "updated_at",
    ],
)
def test_client_cannot_set_internal_fields(field):
    payload = valid_request()
    payload[field] = "unauthorized"
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not-an-object",
        123,
        True,
    ],
)
def test_reservation_body_must_be_object(payload):
    with pytest.raises(ReservationValidationError):
        validate_reservation_request(payload)


def test_all_booking_states_defined():
    assert RESERVATION_STATUSES == {
        "pending",
        "confirmed",
        "rejected",
        "cancelled",
        "completed",
        "no_show",
    }
    assert ACTIVE_RESERVATION_STATUSES == {
        "pending", "confirmed"
    }


def test_all_defined_transitions_accepted():
    for current, targets in RESERVATION_TRANSITIONS.items():
        for target in targets:
            assert validate_reservation_transition(
                current, target
            ) == target


def test_completed_rejected_cancelled_and_no_show_are_terminal():
    for status in (
        "completed", "rejected", "cancelled", "no_show"
    ):
        assert not RESERVATION_TRANSITIONS[status]
        for target in RESERVATION_STATUSES:
            with pytest.raises(ReservationTransitionError):
                validate_reservation_transition(status, target)


def test_every_state_rejects_self_transition():
    for status in RESERVATION_STATUSES:
        with pytest.raises(ReservationTransitionError):
            validate_reservation_transition(status, status)


@pytest.mark.parametrize(
    "current,target",
    [
        ("pending", "completed"),
        ("pending", "no_show"),
        ("confirmed", "pending"),
        ("confirmed", "rejected"),
        ("cancelled", "confirmed"),
        ("rejected", "pending"),
        ("completed", "confirmed"),
    ],
)
def test_forbidden_transitions_rejected(current, target):
    with pytest.raises(ReservationTransitionError):
        validate_reservation_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (None, "confirmed"),
        ("pending", None),
        ("invalid", "confirmed"),
        ("pending", "invalid"),
        (123, "confirmed"),
        ("pending", 123),
    ],
)
def test_malformed_status_transitions_rejected(current, target):
    with pytest.raises(ReservationTransitionError):
        validate_reservation_transition(current, target)


def test_schema_does_not_claim_availability_or_booking_success():
    result = validate_reservation_request(valid_request())
    for field in (
        "available_slots",
        "reservation_confirmed",
        "assigned_table",
        "booking_approved",
        "notification_sent",
    ):
        assert field not in result
