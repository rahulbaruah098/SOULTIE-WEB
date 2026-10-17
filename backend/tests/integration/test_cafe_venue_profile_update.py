"""Security contracts for controlled cafe venue profile updates."""

from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import PyMongoError
import pytest

from app.repositories import venue_repository as repository
from app.schemas.venue import VenueValidationError
from app.security.cafe_partner_access import CafePartnerAccessDenied
from app.services import cafe_venue_management_service as service


PARTNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_PARTNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"


@pytest.fixture
def context(monkeypatch):
    authorize = Mock(return_value={
        "account_type": "cafe_partner",
        "role": "cafe_partner",
        "partner_id": PARTNER,
        "venue_id": VENUE,
    })
    update = Mock(return_value={
        "_id": ObjectId(VENUE),
        "owner_id": PARTNER,
        "status": "pending",
    })

    monkeypatch.setattr(
        service, "authorize_cafe_partner", authorize
    )
    monkeypatch.setattr(
        repository, "update_owned_venue_profile", update
    )

    return SimpleNamespace(
        authorize=authorize,
        update=update,
    )


def apply(changes=None, *, partner=PARTNER, venue=VENUE):
    if changes is None:
        changes = {"description": "New cafe description"}
    return service.update_cafe_owner_venue_profile(
        partner_id=partner,
        venue_id=venue,
        changes=changes,
    )


def test_owner_update_returns_pending_acknowledgement(context):
    changes = {"description": "New cafe description"}
    result = apply(changes)

    assert result == {
        "venue_id": VENUE,
        "status": "pending",
        "requires_reapproval": True,
        "message": "Venue changes submitted for review.",
    }
    context.authorize.assert_called_once_with(
        partner_id=PARTNER, venue_id=VENUE
    )
    context.update.assert_called_once_with(
        VENUE,
        owner_id=PARTNER,
        changes=changes,
    )


def test_update_does_not_choose_approval_status(context):
    result = apply()
    assert result["status"] == "pending"
    assert result["requires_reapproval"] is True
    assert "approved" not in result.values()


def test_owner_and_venue_always_come_from_arguments(context):
    changes = {"venue_name": "Updated Cafe"}
    apply(changes)
    context.update.assert_called_once_with(
        VENUE,
        owner_id=PARTNER,
        changes=changes,
    )


def test_no_sensitive_data_returned(context):
    result = apply()
    for name in (
        "owner_id", "password_hash", "email",
        "review_reason", "reviewed_by", "access_token",
    ):
        assert name not in result


def test_authorization_failure_prevents_database_update(context):
    context.authorize.side_effect = CafePartnerAccessDenied(
        "Account suspended"
    )
    with pytest.raises(CafePartnerAccessDenied):
        apply()
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "bad_context",
    [
        None,
        {},
        {"account_type": "dating_user",
         "partner_id": PARTNER, "venue_id": VENUE},
        {"account_type": "superadmin",
         "partner_id": PARTNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": OTHER_PARTNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": PARTNER, "venue_id": OTHER_VENUE},
    ],
)
def test_invalid_live_identity_rejected_without_write(
    context, bad_context
):
    context.authorize.return_value = bad_context
    with pytest.raises(service.CafeVenueManagementDenied):
        apply()
    context.update.assert_not_called()


@pytest.mark.parametrize(
    "partner,venue",
    [
        ("invalid", VENUE),
        (PARTNER, "invalid"),
        (None, VENUE),
        (PARTNER, None),
        (123, VENUE),
        (PARTNER, 123),
    ],
)
def test_invalid_ids_rejected_without_write(
    context, partner, venue
):
    context.authorize.return_value = {
        "account_type": "cafe_partner",
        "partner_id": partner,
        "venue_id": venue,
    }
    with pytest.raises(service.CafeVenueManagementDenied):
        apply(partner=partner, venue=venue)
    context.update.assert_not_called()


def test_repository_missing_venue_denied(context):
    context.update.return_value = None
    with pytest.raises(service.CafeVenueManagementDenied):
        apply()


def test_repository_database_failure_not_hidden(context):
    context.update.side_effect = PyMongoError("offline")
    with pytest.raises(PyMongoError):
        apply()


def test_repository_receives_only_requested_fields(context):
    changes = {"contact_phone": "+919876543210"}
    apply(changes)
    assert context.update.call_args.kwargs["changes"] == changes


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "approved"},
        {"owner_id": OTHER_PARTNER},
        {"active": True},
        {"reviewed_by": PARTNER},
        {"status_version": 999},
        {"password_hash": "unsafe"},
        {},
    ],
)
def test_schema_rejects_admin_only_and_empty_changes(changes):
    with pytest.raises(VenueValidationError):
        repository.validate_venue_profile(changes)


def test_existing_repository_enforces_pending_transition():
    """Verify the source-level repository update contract.

    Full MongoDB integration will be tested separately.
    """
    import ast
    import inspect

    source = inspect.getsource(
        repository.update_owned_venue_profile
    )
    tree = ast.parse(source)

    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
    ]

    assert any(
        any(
            isinstance(key, ast.Constant)
            and key.value == "status"
            and isinstance(value, ast.Name)
            and value.id == "VENUE_STATUS_PENDING"
            for key, value in zip(
                dictionary.keys,
                dictionary.values,
            )
        )
        for dictionary in assignments
    )


def test_repository_update_failure_does_not_return_success(context):
    context.update.side_effect = VenueValidationError(
        "Unsupported venue fields"
    )
    with pytest.raises(VenueValidationError):
        apply({"status": "approved"})


def test_different_authorized_owner_scoped_to_own_venue(context):
    context.authorize.return_value = {
        "account_type": "cafe_partner",
        "partner_id": OTHER_PARTNER,
        "venue_id": OTHER_VENUE,
    }
    result = apply(
        {"venue_name": "Other Cafe"},
        partner=OTHER_PARTNER,
        venue=OTHER_VENUE,
    )
    assert result["venue_id"] == OTHER_VENUE
    context.update.assert_called_once_with(
        OTHER_VENUE,
        owner_id=OTHER_PARTNER,
        changes={"venue_name": "Other Cafe"},
    )
