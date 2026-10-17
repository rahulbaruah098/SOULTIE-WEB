"""Owner authorization and write restrictions for cafe operations."""

from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import PyMongoError
import pytest

from app.schemas.cafe_venue_operations import CafeVenueOperationsError
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services import cafe_venue_operations_service as service


OWNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_OWNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"


@pytest.fixture
def context(monkeypatch):
    authorize = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": OWNER,
        "venue_id": VENUE,
    })
    persist = Mock(return_value={
        "_id": ObjectId(VENUE),
        "owner_id": OWNER,
        "status": "approved",
        "active": True,
        "facilities": ["parking", "wifi"],
    })

    monkeypatch.setattr(
        service, "authorize_cafe_partner", authorize
    )
    monkeypatch.setattr(
        service, "update_owned_cafe_operations", persist
    )

    return SimpleNamespace(authorize=authorize, persist=persist)


def call(
    changes=None,
    *,
    owner=OWNER,
    venue=VENUE,
):
    if changes is None:
        changes = {"facilities": ["wifi", "parking"]}
    return service.update_cafe_owner_operations(
        partner_id=owner,
        venue_id=venue,
        changes=changes,
    )


def test_valid_facility_update(context):
    result = call()

    assert result == {
        "venue_id": VENUE,
        "facilities": ["parking", "wifi"],
        "status": "approved",
        "message": "Cafe facilities updated.",
    }
    context.authorize.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
    )
    context.persist.assert_called_once_with(
        partner_id=OWNER,
        venue_id=VENUE,
        changes={"facilities": ["parking", "wifi"]},
    )


def test_service_returns_no_owner_secrets(context):
    result = call()
    for field in (
        "owner_id", "password_hash", "session_token",
        "email", "reviewed_by", "access_token",
    ):
        assert field not in result


def test_empty_facility_list_allowed(context):
    context.persist.return_value["facilities"] = []
    result = call({"facilities": []})
    assert result["facilities"] == []
    assert context.persist.call_args.kwargs["changes"] == {
        "facilities": []
    }


@pytest.mark.parametrize(
    "owner,venue",
    [
        (None, VENUE),
        ("invalid", VENUE),
        (123, VENUE),
        (OWNER, None),
        (OWNER, "invalid"),
        (OWNER, 123),
    ],
)
def test_invalid_identity_rejected_before_authorization(
    context, owner, venue
):
    with pytest.raises(service.CafeVenueOperationsDenied):
        call(owner=owner, venue=venue)

    context.authorize.assert_not_called()
    context.persist.assert_not_called()


def test_suspended_account_denied_before_write(context):
    context.authorize.side_effect = CafePartnerAccessDenied(
        "Suspended"
    )
    with pytest.raises(CafePartnerAccessDenied):
        call()
    context.persist.assert_not_called()


@pytest.mark.parametrize(
    "bad_context",
    [
        None,
        {},
        [],
        {
            "account_type": "dating_user",
            "partner_id": OWNER,
            "venue_id": VENUE,
        },
        {
            "account_type": "superadmin",
            "partner_id": OWNER,
            "venue_id": VENUE,
        },
        {
            "account_type": "cafe_partner",
            "partner_id": OTHER_OWNER,
            "venue_id": VENUE,
        },
        {
            "account_type": "cafe_partner",
            "partner_id": OWNER,
            "venue_id": OTHER_VENUE,
        },
    ],
)
def test_wrong_live_authorization_context_rejected(
    context, bad_context
):
    context.authorize.return_value = bad_context

    with pytest.raises(service.CafeVenueOperationsDenied):
        call()

    context.persist.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        [],
        "invalid",
        {"status": "approved"},
        {"owner_id": OTHER_OWNER},
        {"active": True},
        {"booking_status": "confirmed"},
        {"facilities": ["wifi", "wifi"]},
        {"facilities": ["unknown"]},
    ],
)
def test_invalid_or_protected_fields_rejected_before_write(
    context, invalid
):
    with pytest.raises(CafeVenueOperationsError):
        call(invalid)

    context.persist.assert_not_called()


@pytest.mark.parametrize(
    "restricted",
    [
        {
            "booking_settings": {
                "max_party_size": 8,
                "slot_minutes": 60,
                "advance_booking_days": 30,
                "buffer_minutes": 15,
            }
        },
        {
            "weekly_hours": {
                day: [] for day in (
                    "monday", "tuesday", "wednesday",
                    "thursday", "friday",
                    "saturday", "sunday",
                )
            }
        },
        {
            "facilities": ["wifi"],
            "booking_settings": {
                "max_party_size": 8,
                "slot_minutes": 60,
                "advance_booking_days": 30,
                "buffer_minutes": 15,
            },
        },
    ],
)
def test_booking_sensitive_settings_fail_closed(
    context, restricted
):
    with pytest.raises(service.CafeVenueOperationsConflict):
        call(restricted)

    context.persist.assert_not_called()


def test_missing_owned_venue_rejected(context):
    context.persist.return_value = None

    with pytest.raises(service.CafeVenueOperationsDenied):
        call()


@pytest.mark.parametrize(
    "bad_result",
    [
        {},
        [],
        {
            "_id": ObjectId(OTHER_VENUE),
            "owner_id": OWNER,
            "status": "approved",
            "active": True,
            "facilities": ["parking", "wifi"],
        },
        {
            "_id": ObjectId(VENUE),
            "owner_id": OTHER_OWNER,
            "status": "approved",
            "active": True,
            "facilities": ["parking", "wifi"],
        },
        {
            "_id": ObjectId(VENUE),
            "owner_id": OWNER,
            "status": "pending",
            "active": True,
            "facilities": ["parking", "wifi"],
        },
        {
            "_id": ObjectId(VENUE),
            "owner_id": OWNER,
            "status": "approved",
            "active": False,
            "facilities": ["parking", "wifi"],
        },
        {
            "_id": ObjectId(VENUE),
            "owner_id": OWNER,
            "status": "approved",
            "active": True,
            "facilities": ["wifi"],
        },
    ],
)
def test_wrong_database_result_never_acknowledged(
    context, bad_result
):
    context.persist.return_value = bad_result

    with pytest.raises(service.CafeVenueOperationsDenied):
        call()


def test_database_outage_not_reported_as_success(context):
    context.persist.side_effect = PyMongoError(
        "Simulated database outage"
    )

    with pytest.raises(PyMongoError):
        call()


def test_repository_is_never_called_before_live_authorization(
    context,
):
    context.authorize.side_effect = CafePartnerAccessDenied(
        "Approval revoked"
    )
    with pytest.raises(CafePartnerAccessDenied):
        call()
    context.persist.assert_not_called()


def test_different_owner_remains_strictly_scoped(context):
    context.authorize.return_value = {
        "account_type": "cafe_partner",
        "partner_id": OTHER_OWNER,
        "venue_id": OTHER_VENUE,
    }
    context.persist.return_value = {
        "_id": ObjectId(OTHER_VENUE),
        "owner_id": OTHER_OWNER,
        "status": "approved",
        "active": True,
        "facilities": ["wifi"],
    }

    result = call(
        {"facilities": ["wifi"]},
        owner=OTHER_OWNER,
        venue=OTHER_VENUE,
    )

    assert result["venue_id"] == OTHER_VENUE
    context.persist.assert_called_once_with(
        partner_id=OTHER_OWNER,
        venue_id=OTHER_VENUE,
        changes={"facilities": ["wifi"]},
    )
