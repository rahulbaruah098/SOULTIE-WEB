"""Phase 2H realtime token/session security regressions.

All repositories and token-decoding boundaries are mocked.
No real MongoDB records are accessed.
"""

from datetime import datetime, timedelta, timezone
from bson import ObjectId
import pytest

from app.realtime import auth as security
from app.security.tokens import InvalidAccessToken


@pytest.fixture
def valid_identity(monkeypatch):
    user_id = ObjectId()
    sid = "phase2h-valid-session"

    state = {
        "session": {
            "user_id": user_id,
            "revoked_at": None,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "user": {
            "_id": user_id,
            "account_status": "active",
        },
        "payload": {
            "sub": str(user_id),
            "sid": sid,
        },
    }

    monkeypatch.setattr(
        security, "get_session_by_id",
        lambda requested: state["session"] if requested == sid else None
    )
    monkeypatch.setattr(
        security, "get_user_by_id",
        lambda requested: state["user"] if requested == user_id else None
    )
    monkeypatch.setattr(
        security, "decode_access_token",
        lambda token: state["payload"]
    )

    return state, user_id, sid


def test_valid_token_and_active_session(valid_identity):
    state, user_id, sid = valid_identity
    result = security.authenticate_realtime_connection(
        {"access_token": "qa-valid-token"}
    )
    assert result["user_id"] == user_id
    assert result["session_id"] == sid


@pytest.mark.parametrize("auth", [
    None, {}, {"token": "not-supported"},
    {"access_token": ""}, {"access_token": "Bearer "},
])
def test_missing_or_unsupported_credentials_rejected(auth):
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(auth)
    assert error.value.code == "REALTIME_AUTHENTICATION_REQUIRED"


def test_bearer_authorization_accepted(valid_identity):
    state, user_id, sid = valid_identity
    result = security.authenticate_realtime_connection(
        {"authorization": "Bearer qa-valid-token"}
    )
    assert result["session_id"] == sid


def test_invalid_access_token_rejected(valid_identity, monkeypatch):
    def reject(_token):
        raise InvalidAccessToken()

    monkeypatch.setattr(security, "decode_access_token", reject)
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(
            {"access_token": "invalid-token"}
        )
    assert error.value.code == "INVALID_ACCESS_TOKEN"


def test_revoked_session_rejected(valid_identity):
    state, _, _ = valid_identity
    state["session"]["revoked_at"] = datetime.now(timezone.utc)
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(
            {"access_token": "qa-valid-token"}
        )
    assert error.value.code == "INVALID_ACCESS_TOKEN"


def test_expired_session_rejected(valid_identity):
    state, _, _ = valid_identity
    state["session"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(
            {"access_token": "qa-valid-token"}
        )
    assert error.value.code == "INVALID_ACCESS_TOKEN"


def test_session_user_mismatch_rejected(valid_identity):
    state, _, _ = valid_identity
    state["session"]["user_id"] = ObjectId()
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(
            {"access_token": "qa-valid-token"}
        )
    assert error.value.code == "INVALID_ACCESS_TOKEN"


def test_suspended_account_rejected(valid_identity):
    state, _, _ = valid_identity
    state["user"]["account_status"] = "suspended"
    with pytest.raises(security.RealtimeAuthError) as error:
        security.authenticate_realtime_connection(
            {"access_token": "qa-valid-token"}
        )
    assert error.value.code == "ACCOUNT_UNAVAILABLE"


def test_revalidation_detects_revocation(valid_identity):
    state, user_id, sid = valid_identity

    assert security.validate_realtime_identity(
        user_id=user_id, session_id=sid
    )["session_id"] == sid

    state["session"]["revoked_at"] = datetime.now(timezone.utc)

    with pytest.raises(security.RealtimeAuthError) as error:
        security.validate_realtime_identity(
            user_id=user_id, session_id=sid
        )
    assert error.value.code == "INVALID_ACCESS_TOKEN"
