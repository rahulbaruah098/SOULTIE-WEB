"""Purpose-isolated email verification for Soultie cafe owners.

Codes issued here cannot authenticate a dating user or an admin.
No approval or login session is issued by this module.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import secrets

from bson import ObjectId
from flask import current_app
from redis.exceptions import RedisError

from app.extensions import mongo, redis_store
from app.services.cafe_partner_auth_service import _email
from app.services.email_sender import send_email


OTP_TTL = 600
RESEND_COOLDOWN = 60
REQUEST_WINDOW = 3600
MAX_REQUESTS = 5
MAX_ATTEMPTS = 5

_REQUEST_SCRIPT = """
local challenge = KEYS[1]
local cooldown = KEYS[2]
local rate = KEYS[3]
if redis.call('EXISTS', cooldown) == 1 then
    return -1
end
local count = redis.call('INCR', rate)
if count == 1 then
    redis.call('EXPIRE', rate, ARGV[4])
end
if count > tonumber(ARGV[5]) then
    return -2
end
redis.call('SET', challenge, ARGV[1], 'EX', ARGV[2])
redis.call('SET', cooldown, '1', 'EX', ARGV[3])
return 1
"""

_VERIFY_SCRIPT = """
local stored = redis.call('GET', KEYS[1])
if not stored then
    return -1
end
local attempts = redis.call('INCR', KEYS[2])
if attempts == 1 then
    redis.call('EXPIRE', KEYS[2], ARGV[2])
end
if attempts > tonumber(ARGV[3]) then
    redis.call('DEL', KEYS[1])
    return -3
end
if stored ~= ARGV[1] then
    if attempts >= tonumber(ARGV[3]) then
        redis.call('DEL', KEYS[1])
        return -3
    end
    return -2
end
redis.call('DEL', KEYS[1], KEYS[2])
return 1
"""

_INVALIDATE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""


class CafeEmailVerificationError(ValueError):
    """Invalid account or verification request."""


class CafeEmailOtpLimited(CafeEmailVerificationError):
    """Cooldown, request limit, or guessing limit reached."""


class CafeEmailOtpUnavailable(RuntimeError):
    """Verification infrastructure is temporarily unavailable."""


def _keys(email):
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    prefix = "xuoroni:cafe:email-verify:" + digest
    return (
        prefix + ":challenge",
        prefix + ":cooldown",
        prefix + ":rate",
        prefix + ":attempts",
    )


def _digest(email, code):
    secret = current_app.config.get("OTP_HASH_SECRET")
    if not isinstance(secret, str) or len(secret) < 32:
        raise CafeEmailOtpUnavailable(
            "Verification configuration unavailable."
        )
    message = ("cafe-email-verify:" + email + ":" + code).encode()
    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _eligible_account(email):
    account = mongo.db.cafe_partner_accounts.find_one({
        "email": email,
        "account_type": "cafe_partner",
        "status": "pending",
        "email_verified": False,
    })
    if (
        not account
        or not isinstance(account.get("_id"), ObjectId)
        or not isinstance(account.get("venue_id"), ObjectId)
    ):
        raise CafeEmailVerificationError(
            "Cafe email verification is not available for this account."
        )
    return account


def request_cafe_email_verification(*, email):
    """Send a single-use code to a registered pending cafe owner."""
    normalized = _email(email)
    _eligible_account(normalized)
    code = f"{secrets.randbelow(1000000):06d}"
    digest = _digest(normalized, code)
    challenge, cooldown, rate, _ = _keys(normalized)

    try:
        redis = redis_store.get_client()
        outcome = redis.eval(
            _REQUEST_SCRIPT,
            3,
            challenge,
            cooldown,
            rate,
            digest,
            OTP_TTL,
            RESEND_COOLDOWN,
            REQUEST_WINDOW,
            MAX_REQUESTS,
        )
    except (RedisError, RuntimeError) as error:
        raise CafeEmailOtpUnavailable(
            "Verification protection is unavailable."
        ) from error

    if outcome in (-1, -2):
        raise CafeEmailOtpLimited(
            "Please wait before requesting another verification code."
        )
    if outcome != 1:
        raise CafeEmailOtpUnavailable(
            "Verification protection returned an invalid response."
        )

    try:
        send_email(
            recipient=normalized,
            subject="Verify your Xuoroni cafe partner email",
            text_body=(
                "Your Xuoroni cafe verification code is "
                + code
                + ". It expires in 10 minutes. "
                "Do not share this code with anyone."
            ),
        )
    except Exception:
        # Delete only this exact challenge. A newer code, if any,
        # must never be removed by an older failed delivery.
        try:
            redis.eval(
                _INVALIDATE_SCRIPT,
                1,
                challenge,
                digest,
            )
        except RedisError:
            current_app.logger.warning(
                "Unable to invalidate failed cafe email delivery"
            )
        raise CafeEmailOtpUnavailable(
            "Verification email could not be delivered."
        ) from None

    return {"status": "sent", "expires_in": OTP_TTL}


def verify_cafe_email_code(*, email, code):
    """Consume the code, then verify only its linked pending account."""
    normalized = _email(email)
    if (
        not isinstance(code, str)
        or len(code) != 6
        or not code.isascii()
        or not code.isdecimal()
    ):
        raise CafeEmailVerificationError(
            "Invalid email verification code."
        )

    account = _eligible_account(normalized)
    digest = _digest(normalized, code)
    challenge, _, _, attempts = _keys(normalized)

    try:
        outcome = redis_store.get_client().eval(
            _VERIFY_SCRIPT,
            2,
            challenge,
            attempts,
            digest,
            OTP_TTL,
            MAX_ATTEMPTS,
        )
    except (RedisError, RuntimeError) as error:
        raise CafeEmailOtpUnavailable(
            "Verification protection is unavailable."
        ) from error

    if outcome == -3:
        raise CafeEmailOtpLimited(
            "Too many incorrect verification attempts."
        )
    if outcome in (-1, -2):
        raise CafeEmailVerificationError(
            "Invalid or expired email verification code."
        )
    if outcome != 1:
        raise CafeEmailOtpUnavailable(
            "Verification protection returned an invalid response."
        )

    # Compare-and-set prevents approving another account or
    # changing an account already moderated during verification.
    result = mongo.db.cafe_partner_accounts.update_one(
        {
            "_id": account["_id"],
            "email": normalized,
            "account_type": "cafe_partner",
            "status": "pending",
            "email_verified": False,
            "venue_id": account["venue_id"],
        },
        {
            "$set": {
                "email_verified": True,
                "email_verified_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if result.matched_count != 1:
        raise CafeEmailVerificationError(
            "Cafe email verification is no longer available."
        )

    return {
        "status": "verified",
        "email_verified": True,
    }
