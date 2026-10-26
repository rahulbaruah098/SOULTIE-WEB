"""Redis-backed limits for public Soultie cafe registration.

Use only Flask's configured, trusted remote address. Do not parse
client-supplied forwarding headers here.
"""

import hashlib
from ipaddress import ip_address

from flask import current_app, request
from redis.exceptions import RedisError

from app.extensions import redis_store


DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 3600
MAX_LIMIT = 100
MAX_WINDOW_SECONDS = 86400

# Redis evaluates the counter and sets expiration atomically.
# A full window is created on the first request.
_COUNTER_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class CafeRegistrationLimitError(RuntimeError):
    """Cannot reliably enforce the registration limit."""


class CafeRegistrationRateExceeded(CafeRegistrationLimitError):
    """Registration request limit has been reached."""


def _positive_integer(value, *, default, maximum):
    if value is None:
        return default
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise CafeRegistrationLimitError(
            "Invalid cafe registration rate-limit configuration."
        )
    return value


def check_cafe_registration_limit():
    """Charge one registration attempt, or reject it.

    Raise CafeRegistrationRateExceeded for HTTP 429.
    Raise CafeRegistrationLimitError for HTTP 503.
    This method must run before validating credentials or
    writing registration records.
    """
    limit = _positive_integer(
        current_app.config.get("CAFE_REGISTRATION_LIMIT"),
        default=DEFAULT_LIMIT,
        maximum=MAX_LIMIT,
    )
    window = _positive_integer(
        current_app.config.get("CAFE_REGISTRATION_WINDOW_SECONDS"),
        default=DEFAULT_WINDOW_SECONDS,
        maximum=MAX_WINDOW_SECONDS,
    )

    supplied_ip = request.remote_addr
    try:
        if not isinstance(supplied_ip, str):
            raise ValueError("IP address unavailable")
        client_ip = ip_address(supplied_ip)
    except ValueError as error:
        raise CafeRegistrationLimitError(
            "Registration client address unavailable."
        ) from error

    # Never store readable client IP addresses in Redis keys.
    digest = hashlib.sha256(
        client_ip.packed
    ).hexdigest()
    key = "xuoroni:cafe:register:ip:" + digest

    try:
        redis = redis_store.get_client()
        result = redis.eval(_COUNTER_SCRIPT, 1, key, window)
        if isinstance(result, bool) or not isinstance(result, int):
            raise CafeRegistrationLimitError(
                "Registration limit counter returned invalid data."
            )
    except RedisError as error:
        raise CafeRegistrationLimitError(
            "Registration protection temporarily unavailable."
        ) from error
    except (ConnectionError, TimeoutError) as error:
        raise CafeRegistrationLimitError(
            "Registration protection temporarily unavailable."
        ) from error
    except RuntimeError as error:
        raise CafeRegistrationLimitError(
            "Registration protection temporarily unavailable."
        ) from error

    if result > limit:
        raise CafeRegistrationRateExceeded(
            "Too many cafe registration attempts. Try again later."
        )

    return None
