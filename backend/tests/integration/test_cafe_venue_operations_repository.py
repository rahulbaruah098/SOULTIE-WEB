"""Permanent owner-scoped cafe operations repository tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError
import pytest

from app.repositories import (
    cafe_venue_operations_repository as repository,
)
from app.schemas.cafe_venue_operations import (
    CafeVenueOperationsError,
    DAYS,
)


PARTNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_PARTNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"
NOW = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def context(monkeypatch):
    collection = Mock()
    collection.find_one_and_update.return_value = {
        "_id": ObjectId(VENUE),
        "owner_id": PARTNER,
        "status": "approved",
        "active": True,
        "facilities": ["parking", "wifi"],
    }
    monkeypatch.setattr(
        repository,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(venues=collection)),
    )
    return SimpleNamespace(collection=collection)


_DEFAULT_CHANGES = object()


def update(
    changes=_DEFAULT_CHANGES,
    *,
    partner=PARTNER,
    venue=VENUE,
    now=NOW,
):
    if changes is _DEFAULT_CHANGES:
        changes = {"facilities": ["wifi", "parking"]}
    return repository.update_owned_cafe_operations(
        partner_id=partner,
        venue_id=venue,
        changes=changes,
        now=now,
    )


def test_success_targets_only_approved_active_owned_venue(context):
    result = update()
    assert result["status"] == "approved"

    context.collection.find_one_and_update.assert_called_once()
    query, operation = (
        context.collection.find_one_and_update.call_args.args
    )
    kwargs = context.collection.find_one_and_update.call_args.kwargs

    assert query == {
        "_id": ObjectId(VENUE),
        "owner_id": PARTNER,
        "status": "approved",
        "active": True,
    }
    assert operation == {
        "$set": {
            "facilities": ["parking", "wifi"],
            "updated_at": NOW,
        }
    }
    assert kwargs == {"return_document": ReturnDocument.AFTER}


def test_different_partner_and_venue_are_scoped(context):
    update(
        {"facilities": ["wifi"]},
        partner=OTHER_PARTNER,
        venue=OTHER_VENUE,
    )
    query = context.collection.find_one_and_update.call_args.args[0]
    assert query["_id"] == ObjectId(OTHER_VENUE)
    assert query["owner_id"] == OTHER_PARTNER
    assert query["owner_id"] != PARTNER


def test_no_moderation_or_owner_fields_written(context):
    update()
    operation = context.collection.find_one_and_update.call_args.args[1]
    assert set(operation) == {"$set"}

    for forbidden in (
        "_id", "owner_id", "status", "status_version",
        "active", "reviewed_by", "reviewed_at",
        "review_reason", "account_type",
    ):
        assert forbidden not in operation["$set"]


def test_missing_venue_returns_none(context):
    context.collection.find_one_and_update.return_value = None
    assert update() is None


@pytest.mark.parametrize(
    "value",
    [None, "", "bad", 123, True, [], {}, "f" * 25],
)
def test_invalid_partner_ids_rejected_without_write(context, value):
    with pytest.raises(
        repository.CafeVenueOperationsRepositoryError
    ):
        update(partner=value)
    context.collection.find_one_and_update.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [None, "", "bad", 123, True, [], {}, "f" * 25],
)
def test_invalid_venue_ids_rejected_without_write(context, value):
    with pytest.raises(
        repository.CafeVenueOperationsRepositoryError
    ):
        update(venue=value)
    context.collection.find_one_and_update.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {},
        None,
        [],
        "invalid",
        {"owner_id": OTHER_PARTNER},
        {"status": "approved"},
        {"active": True},
        {"reviewed_by": PARTNER},
        {"booking_status": "confirmed"},
        {"facilities": ["wifi", "wifi"]},
        {"facilities": ["unknown"]},
        {"booking_settings": {"max_party_size": True}},
    ],
)
def test_invalid_changes_rejected_without_write(context, changes):
    with pytest.raises(CafeVenueOperationsError):
        update(changes)
    context.collection.find_one_and_update.assert_not_called()


def test_timestamp_converts_offset_to_utc(context):
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 10, 17, 11, 30, tzinfo=ist)
    update(now=local)
    operation = context.collection.find_one_and_update.call_args.args[1]
    assert operation["$set"]["updated_at"] == NOW
    assert operation["$set"]["updated_at"].utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "2026-10-17",
        123,
        datetime(2026, 10, 17, 6, 0),
        True,
    ],
)
def test_invalid_explicit_timestamp_rejected(context, invalid):
    # Passing None is intentional: it requests the current UTC time.
    if invalid is None:
        update(now=None)
        assert context.collection.find_one_and_update.called
        return

    with pytest.raises(
        repository.CafeVenueOperationsRepositoryError
    ):
        update(now=invalid)
    context.collection.find_one_and_update.assert_not_called()


def test_default_timestamp_is_utc(context):
    before = datetime.now(timezone.utc)
    update(now=None)
    after = datetime.now(timezone.utc)

    operation = context.collection.find_one_and_update.call_args.args[1]
    actual = operation["$set"]["updated_at"]
    assert before <= actual <= after
    assert actual.utcoffset() == timedelta(0)


def test_facilities_are_canonicalized(context):
    update({"facilities": ["wifi", "air_conditioning", "parking"]})
    operation = context.collection.find_one_and_update.call_args.args[1]
    assert operation["$set"]["facilities"] == [
        "air_conditioning", "parking", "wifi",
    ]


def test_weekly_hours_written_without_changing_original(context):
    hours = {
        day: [{"open": "09:00", "close": "18:00"}]
        for day in DAYS
    }
    hours["sunday"] = []
    original = deepcopy(hours)

    update({"weekly_hours": hours})
    operation = context.collection.find_one_and_update.call_args.args[1]

    assert operation["$set"]["weekly_hours"] == original
    assert hours == original
    assert operation["$set"]["weekly_hours"] is not hours


def test_booking_settings_are_allowlisted(context):
    settings = {
        "max_party_size": 8,
        "slot_minutes": 60,
        "advance_booking_days": 30,
        "buffer_minutes": 15,
    }
    update({"booking_settings": settings})
    operation = context.collection.find_one_and_update.call_args.args[1]
    assert operation["$set"]["booking_settings"] == settings
    assert "reservation_confirmed" not in operation["$set"]


def test_storage_outage_propagates_without_false_success(context):
    context.collection.find_one_and_update.side_effect = PyMongoError(
        "simulated outage"
    )
    with pytest.raises(PyMongoError):
        update()


def test_repository_never_creates_booking_documents(context):
    update({"facilities": ["wifi"]})
    context.collection.find_one_and_update.assert_called_once()
    context.collection.insert_one.assert_not_called()
    context.collection.delete_one.assert_not_called()


def test_approved_state_is_query_precondition_not_update(context):
    update()
    query, operation = context.collection.find_one_and_update.call_args.args
    assert query["status"] == "approved"
    assert query["active"] is True
    assert "status" not in operation["$set"]
    assert "active" not in operation["$set"]


def test_update_returns_database_result_without_claiming_availability(
    context,
):
    result = update({"facilities": ["wifi"]})
    assert "available_slots" not in result
    assert "reservation_confirmed" not in result
