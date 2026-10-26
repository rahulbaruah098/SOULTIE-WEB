"""Permanent validation tests for Soultie cafe operational data."""

from copy import deepcopy

import pytest

from app.schemas.cafe_venue_operations import (
    BOOKING_SETTINGS_FIELDS,
    DAYS,
    FACILITY_OPTIONS,
    CafeVenueOperationsError,
    validate_cafe_booking_settings,
    validate_cafe_facilities,
    validate_cafe_operations_update,
    validate_cafe_weekly_hours,
)


def example_hours():
    hours = {
        day: [{"open": "09:00", "close": "18:00"}]
        for day in DAYS
    }
    hours["sunday"] = []
    return hours


def example_booking():
    return {
        "max_party_size": 8,
        "slot_minutes": 60,
        "advance_booking_days": 30,
        "buffer_minutes": 15,
    }


def test_valid_full_operations_configuration():
    payload = {
        "facilities": ["wifi", "parking", "outdoor_seating"],
        "weekly_hours": example_hours(),
        "booking_settings": example_booking(),
    }
    before = deepcopy(payload)
    result = validate_cafe_operations_update(payload)

    assert result["facilities"] == [
        "outdoor_seating",
        "parking",
        "wifi",
    ]
    assert result["weekly_hours"]["sunday"] == []
    assert result["booking_settings"] == example_booking()
    assert payload == before


def test_partial_facilities_update_is_allowed():
    assert validate_cafe_operations_update({
        "facilities": ["wifi"],
    }) == {"facilities": ["wifi"]}


def test_partial_weekly_hours_update_is_allowed():
    hours = example_hours()
    assert validate_cafe_operations_update({
        "weekly_hours": hours,
    }) == {"weekly_hours": hours}


def test_partial_booking_settings_update_is_allowed():
    settings = example_booking()
    assert validate_cafe_operations_update({
        "booking_settings": settings,
    }) == {"booking_settings": settings}


def test_facilities_support_all_declared_options():
    all_facilities = sorted(FACILITY_OPTIONS)
    assert validate_cafe_facilities(all_facilities) == all_facilities


def test_facilities_can_be_empty():
    assert validate_cafe_facilities([]) == []


def test_facilities_are_sorted():
    assert validate_cafe_facilities(
        ["wifi", "air_conditioning", "parking"]
    ) == ["air_conditioning", "parking", "wifi"]


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "wifi",
        {"wifi": True},
        ["wifi", "wifi"],
        ["unknown_facility"],
        ["WiFi"],
        [123],
        [None],
        [True],
        [["wifi"]],
        list(FACILITY_OPTIONS) + ["wifi"],
    ],
)
def test_invalid_facilities_rejected(invalid):
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_facilities(invalid)


def test_every_weekday_must_be_present():
    hours = example_hours()
    del hours["monday"]
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_weekly_hours(hours)


def test_unknown_weekday_rejected():
    hours = example_hours()
    hours["holiday"] = []
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_weekly_hours(hours)


def test_all_days_closed_is_valid():
    hours = {day: [] for day in DAYS}
    assert validate_cafe_weekly_hours(hours) == hours


def test_multiple_non_overlapping_periods_valid():
    hours = example_hours()
    hours["monday"] = [
        {"open": "08:00", "close": "12:00"},
        {"open": "12:00", "close": "16:00"},
        {"open": "17:00", "close": "21:00"},
    ]
    assert validate_cafe_weekly_hours(hours) == hours


@pytest.mark.parametrize(
    "periods",
    [
        "09:00-18:00",
        None,
        {},
        [None],
        [{"open": "09:00"}],
        [{"close": "18:00"}],
        [{"open": "09:00", "close": "18:00", "status": "approved"}],
        [{"open": "18:00", "close": "09:00"}],
        [{"open": "09:00", "close": "09:00"}],
        [{"open": "23:00", "close": "01:00"}],
        [{"open": "24:00", "close": "23:00"}],
        [{"open": "9:00", "close": "18:00"}],
        [{"open": 900, "close": "18:00"}],
        [{"open": None, "close": "18:00"}],
        [
            {"open": "09:00", "close": "13:00"},
            {"open": "12:00", "close": "16:00"},
        ],
        [
            {"open": "14:00", "close": "16:00"},
            {"open": "09:00", "close": "12:00"},
        ],
        [
            {"open": "08:00", "close": "10:00"},
            {"open": "10:00", "close": "12:00"},
            {"open": "13:00", "close": "15:00"},
            {"open": "16:00", "close": "18:00"},
        ],
    ],
)
def test_invalid_daily_periods_rejected(periods):
    hours = example_hours()
    hours["monday"] = periods
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_weekly_hours(hours)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        "weekly",
        {"monday": []},
    ],
)
def test_invalid_weekly_hours_structure_rejected(invalid):
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_weekly_hours(invalid)


def test_booking_settings_boundaries_valid():
    result = validate_cafe_booking_settings({
        "max_party_size": 1,
        "slot_minutes": 15,
        "advance_booking_days": 1,
        "buffer_minutes": 0,
    })
    assert result["max_party_size"] == 1

    result = validate_cafe_booking_settings({
        "max_party_size": 100,
        "slot_minutes": 180,
        "advance_booking_days": 180,
        "buffer_minutes": 120,
    })
    assert result["max_party_size"] == 100


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_party_size", 0),
        ("max_party_size", 101),
        ("max_party_size", True),
        ("max_party_size", 2.5),
        ("slot_minutes", 0),
        ("slot_minutes", 17),
        ("slot_minutes", 181),
        ("slot_minutes", False),
        ("advance_booking_days", 0),
        ("advance_booking_days", 181),
        ("advance_booking_days", "30"),
        ("buffer_minutes", -1),
        ("buffer_minutes", 7),
        ("buffer_minutes", 121),
        ("buffer_minutes", None),
    ],
)
def test_invalid_booking_setting_rejected(field, value):
    settings = example_booking()
    settings[field] = value
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_booking_settings(settings)


def test_booking_settings_require_all_fields():
    for field in BOOKING_SETTINGS_FIELDS:
        settings = example_booking()
        del settings[field]
        with pytest.raises(CafeVenueOperationsError):
            validate_cafe_booking_settings(settings)


def test_booking_settings_reject_extra_fields():
    settings = example_booking()
    settings["owner_id"] = "some-owner"
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_booking_settings(settings)


@pytest.mark.parametrize(
    "invalid",
    [None, [], "operations", {}, 123, True],
)
def test_invalid_operations_payload_rejected(invalid):
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_operations_update(invalid)


@pytest.mark.parametrize(
    "protected",
    [
        "owner_id",
        "venue_id",
        "status",
        "active",
        "reviewed_by",
        "status_version",
        "password_hash",
        "booking_status",
        "account_type",
        "approved",
    ],
)
def test_protected_operations_fields_rejected(protected):
    with pytest.raises(CafeVenueOperationsError):
        validate_cafe_operations_update({
            protected: "unauthorized"
        })


def test_combined_update_does_not_mutate_nested_input():
    hours = example_hours()
    settings = example_booking()
    payload = {
        "facilities": ["wifi", "parking"],
        "weekly_hours": hours,
        "booking_settings": settings,
    }
    original = deepcopy(payload)
    result = validate_cafe_operations_update(payload)

    assert payload == original
    assert result is not payload
    assert result["weekly_hours"] is not hours
    assert result["booking_settings"] is not settings


def test_operations_contract_does_not_grant_booking_availability():
    result = validate_cafe_operations_update({
        "booking_settings": example_booking()
    })

    assert "available_slots" not in result
    assert "reservation_confirmed" not in result
    assert "approval_status" not in result
