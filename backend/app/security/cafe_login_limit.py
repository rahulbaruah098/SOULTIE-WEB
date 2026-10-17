"""Redis-backed login attempt limits for Xuoroni cafe partners.

This is separate from dating-user authentication and cafe registration.
It charges requests before password authentication or session issuance.
"""

import hashlib
from ipaddress import ip_address

from flask import current_app, request
from redis.exceptions import RedisError

from app.extensions import redis_store


DEFAULT_IP_LIMIT = 20
DEFAULT_ACCOUNT_LIMIT = 8
DEFAULT_WINDOW_SECONDS = 3600

MAX_LIMIT = 1000
MAX_WINDOW_SECONDS = 86400


class CafeLoginLimitUnavailable(RuntimeError):
    """Login protection cannot be enforced reliably."""


class CafeLoginRateExceeded(CafeLoginLimitUnavailable):
    """Cafe login attempts have reached their allowed limit."""


_COUNTER_SCRIPT = """
local ip_count = redis.call('INCR', KEYS[1])
if ip_count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end

local account_count = redis.call('INCR', KEYS[2])
if account_count == 1 then
    redis.call('EXPIRE', KEYS[2], ARGV[1])
end

return {ip_count, account_count}
"""


def _positive_integer(value, *, default, maximum):
    if value is None:
        return default
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise CafeLoginLimitUnavailable(
            "Invalid cafe login limit configuration."
        )
    return value


def _account_bucket(email):
    """Hash normalized account identifiers, including invalid inputs."""
    if not isinstance(email, str):
        normalized = "<invalid>"
    elif len(email) > 254:
        normalized = "<oversized>"
    else:
        normalized = email.strip().lower() or "<invalid>"

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def check_cafe_login_limit(*, email):
    """Charge one login attempt across IP and account buckets.

    Execute before any account lookup or password verification.
    Do not use untrusted X-Forwarded-For headers.
    """
    ip_limit = _positive_integer(
        current_app.config.get("CAFE_LOGIN_IP_LIMIT"),
        default=DEFAULT_IP_LIMIT,
        maximum=MAX_LIMIT,
    )
    account_limit = _positive_integer(
        current_app.config.get("CAFE_LOGIN_ACCOUNT_LIMIT"),
        default=DEFAULT_ACCOUNT_LIMIT,
        maximum=MAX_LIMIT,
    )
    window = _positive_integer(
        current_app.config.get("CAFE_LOGIN_WINDOW_SECONDS"),
        default=DEFAULT_WINDOW_SECONDS,
        maximum=MAX_WINDOW_SECONDS,
    )

    raw_ip = request.remote_addr
    try:
        if not isinstance(raw_ip, str):
            raise ValueError("Missing client address")
        parsed_ip = ip_address(raw_ip)
    except ValueError as error:
        raise CafeLoginLimitUnavailable(
            "Client address unavailable."
        ) from error

    ip_hash = hashlib.sha256(parsed_ip.packed).hexdigest()
    email_hash = _account_bucket(email)

    ip_key = "xuoroni:cafe:login:ip:" + ip_hash
    account_key = "xuoroni:cafe:login:account:" + email_hash

    try:
        redis = redis_store.get_client()
        counts = redis.eval(
            _COUNTER_SCRIPT,
            2,
            ip_key,
            account_key,
            window,
        )
    except (RedisError, ConnectionError, TimeoutError) as error:
        raise CafeLoginLimitUnavailable(
            "Cafe login protection temporarily unavailable."
        ) from error
    except RuntimeError as error:
        raise CafeLoginLimitUnavailable(
            "Cafe login protection temporarily unavailable."
        ) from error

    if (
        not isinstance(counts, (list, tuple))
        or len(counts) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            for value in counts
        )
    ):
        raise CafeLoginLimitUnavailable(
            "Cafe login counter returned invalid data."
        )

    if counts[0] > ip_limit or counts[1] > account_limit:
        raise CafeLoginRateExceeded(
            "Too many cafe login attempts. Try again later."
        )

    return None
