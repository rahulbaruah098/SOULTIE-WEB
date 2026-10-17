"""Isolated security tests for cafe-only email verification."""

from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId
from flask import Flask
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services import cafe_email_verification_service as service


EMAIL = "owner@example.com"
CODE = "123456"


@pytest.fixture
def context(monkeypatch):
    app = Flask("xuoroni-cafe-email-tests")
    app.config.update(
        TESTING=True,
        OTP_HASH_SECRET="a" * 48,
    )

    account = {
        "_id": ObjectId(),
        "venue_id": ObjectId(),
        "email": EMAIL,
        "account_type": "cafe_partner",
        "status": "pending",
        "email_verified": False,
    }

    accounts = Mock()
    accounts.find_one.return_value = account
    accounts.update_one.return_value = SimpleNamespace(
        matched_count=1
    )

    redis = Mock()
    redis.eval.return_value = 1

    sender = Mock()

    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(cafe_partner_accounts=accounts)
        ),
    )
    monkeypatch.setattr(
        service,
        "redis_store",
        SimpleNamespace(get_client=lambda: redis),
    )
    monkeypatch.setattr(service, "send_email", sender)
    monkeypatch.setattr(
        service.secrets,
        "randbelow",
        lambda upper: 123456,
    )

    return SimpleNamespace(
        app=app,
        account=account,
        accounts=accounts,
        redis=redis,
        sender=sender,
    )


def test_request_sends_code_to_pending_owner(context):
    ctx = context

    with ctx.app.app_context():
        result = service.request_cafe_email_verification(
            email="OWNER@EXAMPLE.COM"
        )

    assert result == {"status": "sent", "expires_in": 600}
    assert ctx.sender.call_count == 1
    kwargs = ctx.sender.call_args.kwargs
    assert kwargs["recipient"] == EMAIL
    assert CODE in kwargs["text_body"]

    script, key_count, *args = ctx.redis.eval.call_args.args
    assert key_count == 3
    assert "INCR" in script
    assert args[0].startswith("xuoroni:cafe:email-verify:")
    assert "auth:email" not in args[0]
    assert CODE not in str(args)
    ctx.accounts.update_one.assert_not_called()


def test_verification_marks_only_correct_pending_account(context):
    ctx = context

    with ctx.app.app_context():
        result = service.verify_cafe_email_code(
            email=EMAIL, code=CODE
        )

    assert result == {
        "status": "verified",
        "email_verified": True,
    }
    criteria, update = ctx.accounts.update_one.call_args.args

    assert criteria["_id"] == ctx.account["_id"]
    assert criteria["venue_id"] == ctx.account["venue_id"]
    assert criteria["status"] == "pending"
    assert criteria["email_verified"] is False

    assert update["$set"]["email_verified"] is True
    assert "status" not in update["$set"]
    assert "venue_id" not in update["$set"]
    ctx.sender.assert_not_called()


@pytest.mark.parametrize("outcome", [-1, -2])
def test_wrong_or_expired_code_never_updates_account(
    context, outcome
):
    ctx = context
    ctx.redis.eval.return_value = outcome

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailVerificationError):
            service.verify_cafe_email_code(
                email=EMAIL, code=CODE
            )

    ctx.accounts.update_one.assert_not_called()


def test_guessing_limit_rejects_verification(context):
    ctx = context
    ctx.redis.eval.return_value = -3

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpLimited):
            service.verify_cafe_email_code(
                email=EMAIL, code=CODE
            )

    ctx.accounts.update_one.assert_not_called()


@pytest.mark.parametrize("outcome", [-1, -2])
def test_resend_cooldown_and_limit_block_delivery(
    context, outcome
):
    ctx = context
    ctx.redis.eval.return_value = outcome

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpLimited):
            service.request_cafe_email_verification(
                email=EMAIL
            )

    ctx.sender.assert_not_called()


def test_email_delivery_failure_invalidates_challenge(context):
    ctx = context
    ctx.sender.side_effect = RuntimeError(
        "Simulated mail delivery failure"
    )

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpUnavailable):
            service.request_cafe_email_verification(
                email=EMAIL
            )

    assert ctx.redis.eval.call_count == 2
    invalidation = ctx.redis.eval.call_args.args
    assert invalidation[1] == 1
    assert "GET" in invalidation[0]
    assert "DEL" in invalidation[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "approved"),
        ("status", "rejected"),
        ("status", "suspended"),
        ("email_verified", True),
        ("venue_id", None),
        ("account_type", "dating_user"),
    ],
)
def test_ineligible_account_does_not_get_verified(
    context, field, value
):
    ctx = context
    account = dict(ctx.account)
    account[field] = value

    # Mongo normally enforces these query predicates.
    ctx.accounts.find_one.return_value = (
        account
        if (
            account.get("status") == "pending"
            and account.get("email_verified") is False
            and account.get("account_type") == "cafe_partner"
        )
        else None
    )

    with ctx.app.app_context():
        if field == "venue_id":
            with pytest.raises(service.CafeEmailVerificationError):
                service.verify_cafe_email_code(
                    email=EMAIL, code=CODE
                )
        else:
            with pytest.raises(service.CafeEmailVerificationError):
                service.request_cafe_email_verification(
                    email=EMAIL
                )

    ctx.accounts.update_one.assert_not_called()
    ctx.sender.assert_not_called()


@pytest.mark.parametrize(
    "invalid_code",
    ["", "12", "12345", "1234567", "12A456", "??????", None],
)
def test_malformed_code_rejected_without_redis(
    context, invalid_code
):
    ctx = context

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailVerificationError):
            service.verify_cafe_email_code(
                email=EMAIL, code=invalid_code
            )

    ctx.redis.eval.assert_not_called()
    ctx.accounts.update_one.assert_not_called()


def test_redis_outage_blocks_code_request(context):
    ctx = context
    ctx.redis.eval.side_effect = RedisConnectionError(
        "Simulated outage"
    )

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpUnavailable):
            service.request_cafe_email_verification(
                email=EMAIL
            )

    ctx.sender.assert_not_called()


def test_redis_outage_blocks_verification(context):
    ctx = context
    ctx.redis.eval.side_effect = RedisConnectionError(
        "Simulated outage"
    )

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpUnavailable):
            service.verify_cafe_email_code(
                email=EMAIL, code=CODE
            )

    ctx.accounts.update_one.assert_not_called()


def test_stale_account_update_cannot_claim_verification(context):
    ctx = context
    ctx.accounts.update_one.return_value = SimpleNamespace(
        matched_count=0
    )

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailVerificationError):
            service.verify_cafe_email_code(
                email=EMAIL, code=CODE
            )

    assert ctx.accounts.update_one.call_count == 1


def test_missing_hash_secret_prevents_issuance(context):
    ctx = context
    ctx.app.config["OTP_HASH_SECRET"] = "weak"

    with ctx.app.app_context():
        with pytest.raises(service.CafeEmailOtpUnavailable):
            service.request_cafe_email_verification(
                email=EMAIL
            )

    ctx.sender.assert_not_called()
    ctx.redis.eval.assert_not_called()


def test_cafe_keys_do_not_overlap_dating_otp_namespace(context):
    with context.app.app_context():
        keys = service._keys(EMAIL)

    assert len(keys) == 4
    assert len(set(keys)) == 4
    assert all(
        key.startswith("xuoroni:cafe:email-verify:")
        for key in keys
    )
    assert EMAIL not in repr(keys)
