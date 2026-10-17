"""Isolated cafe partner session lifecycle and security tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import hashlib

from bson import ObjectId
import pytest

from app.services import cafe_partner_session_service as service
from app.security.cafe_partner_access import CafePartnerAccessDenied


@pytest.fixture
def context(monkeypatch):
    partner_id = ObjectId()
    venue_id = ObjectId()
    owner_context = {
        "partner_id": str(partner_id),
        "venue_id": str(venue_id),
        "email": "owner@example.com",
        "account_type": "cafe_partner",
        "role": "cafe_partner",
    }

    sessions = Mock()
    sessions.insert_one.return_value = SimpleNamespace(
        inserted_id=ObjectId()
    )
    sessions.find_one.return_value = {
        "partner_id": partner_id,
        "venue_id": venue_id,
    }
    sessions.update_one.return_value = SimpleNamespace(
        modified_count=1
    )

    authorize = Mock(return_value=owner_context)
    monkeypatch.setattr(service, "authorize_cafe_partner", authorize)
    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(cafe_partner_sessions=sessions)
        ),
    )

    return SimpleNamespace(
        partner_id=partner_id,
        venue_id=venue_id,
        owner_context=owner_context,
        sessions=sessions,
        authorize=authorize,
    )


def test_creation_issues_opaque_token(context):
    result = service.create_cafe_session(
        partner_id=str(context.partner_id)
    )
    token = result["access_token"]

    assert isinstance(token, str)
    assert len(token) >= 40
    assert result["token_type"] == "Bearer"
    assert result["account_type"] == "cafe_partner"
    assert result["partner_id"] == str(context.partner_id)
    assert result["venue_id"] == str(context.venue_id)
    assert result["expires_in"] == 12 * 3600
    context.authorize.assert_called_once_with(
        partner_id=str(context.partner_id)
    )


def test_creation_stores_only_token_digest(context):
    result = service.create_cafe_session(
        partner_id=str(context.partner_id)
    )
    document = context.sessions.insert_one.call_args.args[0]
    token = result["access_token"]

    assert document["token_hash"] == hashlib.sha256(
        token.encode("ascii")
    ).hexdigest()
    assert token not in repr(document)
    assert "access_token" not in document
    assert document["session_type"] == "cafe_partner"
    assert document["revoked_at"] is None
    assert document["partner_id"] == context.partner_id
    assert document["venue_id"] == context.venue_id
    assert document["expires_at"] > document["created_at"]
    assert document["expires_at"] - document["created_at"] == (
        timedelta(hours=12)
    )


def test_creation_requires_live_approval(context):
    context.authorize.side_effect = CafePartnerAccessDenied("denied")
    with pytest.raises(CafePartnerAccessDenied):
        service.create_cafe_session(
            partner_id=str(context.partner_id)
        )
    context.sessions.insert_one.assert_not_called()


def test_session_authentication_rechecks_live_account(context):
    token = "A" * 64
    result = service.authenticate_cafe_session(token=token)
    assert result == context.owner_context
    query = context.sessions.find_one.call_args.args[0]
    assert query["token_hash"] == hashlib.sha256(
        token.encode("ascii")
    ).hexdigest()
    assert query["session_type"] == "cafe_partner"
    assert query["revoked_at"] is None
    assert "$gt" in query["expires_at"]
    context.authorize.assert_called_once_with(
        partner_id=str(context.partner_id),
        venue_id=str(context.venue_id),
    )


def test_expired_revoked_or_unknown_session_denied(context):
    context.sessions.find_one.return_value = None
    with pytest.raises(service.CafeSessionInvalid):
        service.authenticate_cafe_session(token="B" * 64)
    context.authorize.assert_not_called()


def test_cafe_suspension_invalidates_session(context):
    context.authorize.side_effect = CafePartnerAccessDenied("suspended")
    with pytest.raises(service.CafeSessionInvalid):
        service.authenticate_cafe_session(token="C" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("partner_id", None),
        ("partner_id", "not-an-object-id"),
        ("venue_id", None),
        ("venue_id", "not-an-object-id"),
    ],
)
def test_corrupted_session_identity_denied(context, field, value):
    document = {
        "partner_id": context.partner_id,
        "venue_id": context.venue_id,
    }
    document[field] = value
    context.sessions.find_one.return_value = document

    with pytest.raises(service.CafeSessionInvalid):
        service.authenticate_cafe_session(token="D" * 64)

    context.authorize.assert_not_called()


@pytest.mark.parametrize(
    "token",
    [None, "", "short", 123, [], {}, "A" * 257, "?" * 50],
)
def test_malformed_tokens_rejected_before_database(context, token):
    with pytest.raises(service.CafeSessionInvalid):
        service.authenticate_cafe_session(token=token)
    context.sessions.find_one.assert_not_called()


def test_revoke_only_presented_cafe_token(context):
    assert service.revoke_cafe_session(token="E" * 64) is True
    query = context.sessions.update_one.call_args.args[0]
    change = context.sessions.update_one.call_args.args[1]

    assert query["session_type"] == "cafe_partner"
    assert query["revoked_at"] is None
    assert query["token_hash"] == hashlib.sha256(
        ("E" * 64).encode("ascii")
    ).hexdigest()
    assert isinstance(change["$set"]["revoked_at"], datetime)
    context.authorize.assert_not_called()


def test_revoke_unknown_or_already_revoked_token(context):
    context.sessions.update_one.return_value = SimpleNamespace(
        modified_count=0
    )
    assert service.revoke_cafe_session(token="F" * 64) is False


def test_two_session_tokens_have_distinct_hashes(context):
    one = service.create_cafe_session(
        partner_id=str(context.partner_id)
    )["access_token"]
    two = service.create_cafe_session(
        partner_id=str(context.partner_id)
    )["access_token"]

    assert one != two
    assert service._digest(one) != service._digest(two)
    assert context.sessions.insert_one.call_count == 2


def test_cafe_tokens_have_no_dating_or_admin_claims(context):
    result = service.create_cafe_session(
        partner_id=str(context.partner_id)
    )
    assert "user_id" not in result
    assert "admin_id" not in result
    assert "refresh_token" not in result
    assert result["account_type"] == "cafe_partner"


def test_cafe_session_rechecks_original_venue(context):
    service.authenticate_cafe_session(token="G" * 64)
    kwargs = context.authorize.call_args.kwargs
    assert kwargs["venue_id"] == str(context.venue_id)
    assert kwargs["partner_id"] == str(context.partner_id)


def test_expiry_query_uses_utc_time(context):
    service.authenticate_cafe_session(token="H" * 64)
    query = context.sessions.find_one.call_args.args[0]
    checked_time = query["expires_at"]["$gt"]
    assert isinstance(checked_time, datetime)
    assert checked_time.tzinfo is not None
    assert checked_time.utcoffset() == timedelta(0)


def test_revoke_invalid_token_does_not_modify_session(context):
    with pytest.raises(service.CafeSessionInvalid):
        service.revoke_cafe_session(token="invalid")
    context.sessions.update_one.assert_not_called()
