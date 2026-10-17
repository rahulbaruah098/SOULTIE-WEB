"""Security tests for cafe partner logout-all session revocation."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from pymongo.errors import PyMongoError
import pytest

from app.services import cafe_partner_session_service as service


@pytest.fixture
def context(monkeypatch):
    owner_id = ObjectId()
    other_id = ObjectId()
    sessions = Mock()
    sessions.update_many.return_value = SimpleNamespace(
        modified_count=3
    )

    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(cafe_partner_sessions=sessions)
        ),
    )

    return SimpleNamespace(
        owner_id=owner_id,
        other_id=other_id,
        sessions=sessions,
    )


def test_revokes_all_unrevoked_sessions_for_owner(context):
    result = service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    assert result == 3
    context.sessions.update_many.assert_called_once()

    query, change = context.sessions.update_many.call_args.args

    assert query == {
        "partner_id": context.owner_id,
        "session_type": "cafe_partner",
        "revoked_at": None,
    }
    assert set(change) == {"$set"}
    assert set(change["$set"]) == {"revoked_at"}
    assert isinstance(change["$set"]["revoked_at"], datetime)
    assert change["$set"]["revoked_at"].tzinfo is not None


def test_other_cafe_not_targeted(context):
    service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    query = context.sessions.update_many.call_args.args[0]
    assert query["partner_id"] != context.other_id


def test_no_dating_or_admin_collection_access(context):
    service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    assert context.sessions.update_many.call_count == 1
    query = context.sessions.update_many.call_args.args[0]
    assert query["session_type"] == "cafe_partner"


def test_zero_current_sessions_returns_zero(context):
    context.sessions.update_many.return_value = SimpleNamespace(
        modified_count=0
    )
    assert service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    ) == 0


@pytest.mark.parametrize(
    "partner_id",
    [
        None,
        "",
        123,
        [],
        {},
        "not-an-object-id",
        "f" * 25,
    ],
)
def test_invalid_partner_id_fails_without_database_write(
    context, partner_id
):
    with pytest.raises(service.CafeSessionInvalid):
        service.revoke_all_cafe_sessions(
            partner_id=partner_id
        )
    context.sessions.update_many.assert_not_called()


def test_valid_bson_id_accepted(context):
    owner = str(context.owner_id)
    count = service.revoke_all_cafe_sessions(
        partner_id=owner
    )
    assert count == 3
    assert (
        context.sessions.update_many.call_args.args[0]
        ["partner_id"] == ObjectId(owner)
    )


def test_database_failure_is_not_reported_as_success(context):
    context.sessions.update_many.side_effect = PyMongoError(
        "simulated outage"
    )
    with pytest.raises(PyMongoError):
        service.revoke_all_cafe_sessions(
            partner_id=str(context.owner_id)
        )


def test_timestamp_is_utc_and_generated_for_revocation(context):
    before = datetime.now(timezone.utc)
    service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    after = datetime.now(timezone.utc)
    timestamp = (
        context.sessions.update_many.call_args.args[1]
        ["$set"]["revoked_at"]
    )
    assert before <= timestamp <= after


def test_different_partner_ids_produce_different_filters(context):
    service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    first = context.sessions.update_many.call_args.args[0]
    service.revoke_all_cafe_sessions(
        partner_id=str(context.other_id)
    )
    second = context.sessions.update_many.call_args.args[0]
    assert first["partner_id"] != second["partner_id"]


def test_existing_revoked_sessions_excluded_from_update(context):
    service.revoke_all_cafe_sessions(
        partner_id=str(context.owner_id)
    )
    query = context.sessions.update_many.call_args.args[0]
    assert query["revoked_at"] is None
    assert "token_hash" not in query
