"""Security tests for live cafe-partner account and venue access."""

from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
import pytest

from app.security import cafe_partner_access as access


@pytest.fixture
def context(monkeypatch):
    partner_id = ObjectId()
    venue_id = ObjectId()

    account = {
        "_id": partner_id,
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
        "venue_id": venue_id,
        "email": "owner@example.com",
    }
    venue = {
        "_id": venue_id,
        "owner_id": str(partner_id),
        "status": "approved",
        "active": True,
    }

    accounts = Mock()
    venues = Mock()
    accounts.find_one.return_value = account
    venues.find_one.return_value = venue

    monkeypatch.setattr(
        access,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(
                cafe_partner_accounts=accounts,
                venues=venues,
            )
        ),
    )

    return SimpleNamespace(
        partner_id=partner_id,
        venue_id=venue_id,
        account=account,
        venue=venue,
        accounts=accounts,
        venues=venues,
    )


def test_approved_verified_partner_authorized(context):
    result = access.authorize_cafe_partner(
        partner_id=str(context.partner_id)
    )

    assert result == {
        "partner_id": str(context.partner_id),
        "venue_id": str(context.venue_id),
        "email": "owner@example.com",
        "account_type": "cafe_partner",
        "role": "cafe_partner",
    }

    context.accounts.find_one.assert_called_once_with({
        "_id": context.partner_id,
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
    })
    context.venues.find_one.assert_called_once_with({
        "_id": context.venue_id,
        "owner_id": str(context.partner_id),
        "status": "approved",
        "active": True,
    })


def test_explicit_own_venue_allowed(context):
    result = access.authorize_cafe_partner(
        partner_id=str(context.partner_id),
        venue_id=str(context.venue_id),
    )
    assert result["venue_id"] == str(context.venue_id)


def test_other_venue_id_denied_before_venue_lookup(context):
    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id),
            venue_id=str(ObjectId()),
        )

    context.venues.find_one.assert_not_called()


@pytest.mark.parametrize(
    "invalid_id",
    [None, "", "invalid", "123", 123, [], {}, True],
)
def test_invalid_partner_id_denied(context, invalid_id):
    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(partner_id=invalid_id)

    context.accounts.find_one.assert_not_called()
    context.venues.find_one.assert_not_called()


@pytest.mark.parametrize(
    "invalid_id",
    ["invalid", "", None, 123, [], {}],
)
def test_invalid_explicit_venue_id_denied(context, invalid_id):
    if invalid_id is None:
        pytest.skip("None means no explicit venue restriction")
    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id),
            venue_id=invalid_id,
        )


def test_missing_partner_denied(context):
    context.accounts.find_one.return_value = None

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )

    context.venues.find_one.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "pending"),
        ("status", "rejected"),
        ("status", "suspended"),
        ("email_verified", False),
        ("account_type", "dating_user"),
    ],
)
def test_unapproved_account_denied(context, field, value):
    account = dict(context.account)
    account[field] = value

    # Simulate Mongo enforcing the account query predicates.
    context.accounts.find_one.return_value = (
        account
        if (
            account["status"] == "approved"
            and account["email_verified"] is True
            and account["account_type"] == "cafe_partner"
        )
        else None
    )

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )

    context.venues.find_one.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "pending"),
        ("status", "rejected"),
        ("status", "suspended"),
        ("active", False),
        ("owner_id", "another-partner"),
    ],
)
def test_unapproved_or_wrongly_owned_venue_denied(
    context, field, value
):
    venue = dict(context.venue)
    venue[field] = value

    # Simulate Mongo enforcing the venue query predicates.
    context.venues.find_one.return_value = (
        venue
        if (
            venue["status"] == "approved"
            and venue["active"] is True
            and venue["owner_id"] == str(context.partner_id)
        )
        else None
    )

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )


def test_missing_venue_denied(context):
    context.venues.find_one.return_value = None

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )


def test_account_venue_link_mismatch_denied(context):
    account = dict(context.account)
    account["venue_id"] = ObjectId()
    context.accounts.find_one.return_value = account

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )


def test_account_without_linked_venue_denied(context):
    account = dict(context.account)
    account["venue_id"] = None
    context.accounts.find_one.return_value = account

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )

    context.venues.find_one.assert_not_called()


def test_missing_account_email_denied(context):
    account = dict(context.account)
    account["email"] = ""
    context.accounts.find_one.return_value = account

    with pytest.raises(access.CafePartnerAccessDenied):
        access.authorize_cafe_partner(
            partner_id=str(context.partner_id)
        )


def test_returned_context_contains_no_privileged_role(context):
    result = access.authorize_cafe_partner(
        partner_id=str(context.partner_id)
    )

    assert result["role"] == "cafe_partner"
    assert result["account_type"] == "cafe_partner"
    assert "access_token" not in result
    assert "admin_id" not in result
    assert "password_hash" not in result
