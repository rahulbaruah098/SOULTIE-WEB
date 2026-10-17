"""Owner-scoped cafe venue profile security regression tests."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
import pytest

from app.services import cafe_venue_management_service as service
from app.security.cafe_partner_access import CafePartnerAccessDenied


PARTNER = "507f1f77bcf86cd799439011"
VENUE = "507f191e810c19729de860ea"
OTHER_PARTNER = "507f1f77bcf86cd799439022"
OTHER_VENUE = "507f191e810c19729de860bb"


@pytest.fixture
def context(monkeypatch):
    authorize = Mock(return_value={
        "account_type": "cafe_partner",
        "partner_id": PARTNER,
        "venue_id": VENUE,
        "role": "cafe_partner",
    })

    venue = {
        "_id": ObjectId(VENUE),
        "owner_id": PARTNER,
        "status": "approved",
        "active": True,
        "venue_name": "Cafe Xuoroni",
        "description": "A welcoming cafe",
        "address": "Assam",
        "city": "Guwahati",
        "state": "Assam",
        "postal_code": "781001",
        "contact_phone": "+919876543210",
        "website": "https://example.org",
        "password_hash": "SECRET_HASH",
        "owner_email": "private@example.org",
        "review_reason": "PRIVATE_REVIEW",
        "internal_notes": "PRIVATE_NOTES",
    }

    get_owned = Mock(return_value=venue)

    monkeypatch.setattr(
        service, "authorize_cafe_partner", authorize
    )
    monkeypatch.setattr(
        service, "get_owned_venue", get_owned
    )

    return SimpleNamespace(
        authorize=authorize,
        get_owned=get_owned,
        venue=venue,
    )


def profile():
    return service.get_cafe_owner_venue_profile(
        partner_id=PARTNER,
        venue_id=VENUE,
    )


def test_valid_approved_venue_profile(context):
    result = profile()
    assert result["venue_id"] == VENUE
    assert result["venue_name"] == "Cafe Xuoroni"
    assert result["status"] == "approved"
    assert result["active"] is True
    assert result["city"] == "Guwahati"
    context.authorize.assert_called_once_with(
        partner_id=PARTNER, venue_id=VENUE
    )
    context.get_owned.assert_called_once_with(
        VENUE, owner_id=PARTNER
    )


def test_profile_output_is_strictly_allowlisted(context):
    result = profile()
    assert set(result) == {
        "venue_id", "status", "active",
        *service.PUBLIC_PROFILE_FIELDS,
    }
    for private in (
        "_id", "owner_id", "owner_email", "password_hash",
        "review_reason", "reviewed_by", "internal_notes",
    ):
        assert private not in result
    assert "SECRET_HASH" not in str(result)
    assert "PRIVATE_REVIEW" not in str(result)


def test_read_does_not_mutate_venue(context):
    before = deepcopy(context.venue)
    profile()
    assert context.venue == before


def test_missing_optional_fields_return_none(context):
    context.venue.pop("description")
    context.venue.pop("website")
    result = profile()
    assert result["description"] is None
    assert result["website"] is None


def test_nontext_optional_field_is_not_exposed(context):
    context.venue["website"] = {"secret": "hidden"}
    assert profile()["website"] is None


def test_live_authorization_denial_prevents_read(context):
    context.authorize.side_effect = CafePartnerAccessDenied(
        "Cafe access denied."
    )
    with pytest.raises(CafePartnerAccessDenied):
        profile()
    context.get_owned.assert_not_called()


@pytest.mark.parametrize(
    "bad_context",
    [
        None,
        {},
        {"account_type": "dating_user",
         "partner_id": PARTNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": OTHER_PARTNER, "venue_id": VENUE},
        {"account_type": "cafe_partner",
         "partner_id": PARTNER, "venue_id": OTHER_VENUE},
    ],
)
def test_invalid_authorization_context_fails_closed(
    context, bad_context
):
    context.authorize.return_value = bad_context
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()
    context.get_owned.assert_not_called()


@pytest.mark.parametrize(
    "status",
    ["pending", "rejected", "suspended", None],
)
def test_nonapproved_venue_denied(context, status):
    context.venue["status"] = status
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


@pytest.mark.parametrize("active", [False, None, 1, "true"])
def test_inactive_or_invalid_active_flag_denied(context, active):
    context.venue["active"] = active
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


@pytest.mark.parametrize(
    "record",
    [None, {}, [], "not a venue"],
)
def test_missing_or_invalid_venue_record_denied(context, record):
    context.get_owned.return_value = record
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


def test_different_venue_id_denied(context):
    context.venue["_id"] = ObjectId(OTHER_VENUE)
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


def test_different_owner_id_denied(context):
    context.venue["owner_id"] = OTHER_PARTNER
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


@pytest.mark.parametrize(
    "name",
    [None, "", 123, [], {}],
)
def test_missing_or_invalid_name_denied(context, name):
    context.venue["venue_name"] = name
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()


def test_does_not_query_another_partner_record(context):
    context.get_owned.return_value = {
        **context.venue,
        "owner_id": OTHER_PARTNER,
    }
    with pytest.raises(service.CafeVenueManagementDenied):
        profile()
    context.get_owned.assert_called_once_with(
        VENUE, owner_id=PARTNER
    )
