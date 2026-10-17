"""Security tests for isolated cafe login limits."""

import hashlib
from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.security import cafe_login_limit as limiter


@pytest.fixture
def context(monkeypatch):
    app = Flask("isolated-cafe-login-limiter")
    app.config["TESTING"] = True
    redis = Mock()
    redis.eval.return_value = [1, 1]
    store = SimpleNamespace(get_client=Mock(return_value=redis))
    monkeypatch.setattr(limiter, "redis_store", store)

    def charge(email="owner@example.com", ip="127.0.0.1"):
        with app.test_request_context(
            "/api/v1/cafe-partners/login",
            method="POST",
            environ_overrides={"REMOTE_ADDR": ip},
        ):
            return limiter.check_cafe_login_limit(email=email)

    return SimpleNamespace(
        app=app, redis=redis, store=store, charge=charge
    )


def test_both_buckets_charged(context):
    assert context.charge() is None
    args = context.redis.eval.call_args.args
    assert args[1] == 2
    assert args[4] == 3600
    assert args[2].startswith("xuoroni:cafe:login:ip:")
    assert args[3].startswith("xuoroni:cafe:login:account:")
    assert "owner@example.com" not in repr(args)
    assert "127.0.0.1" not in repr(args)


def test_ip_hash_is_correct(context):
    context.charge(ip="192.0.2.14")
    first = context.redis.eval.call_args.args[2]
    expected = hashlib.sha256(
        ip_address("192.0.2.14").packed
    ).hexdigest()
    assert first == "xuoroni:cafe:login:ip:" + expected
    context.charge(ip="192.0.2.15")
    assert first != context.redis.eval.call_args.args[2]


def test_email_normalization_and_account_isolation(context):
    context.charge(email=" OWNER@EXAMPLE.COM ")
    first = context.redis.eval.call_args.args[3]
    context.charge(email="owner@example.com")
    assert first == context.redis.eval.call_args.args[3]
    context.charge(email="other@example.com")
    assert first != context.redis.eval.call_args.args[3]


@pytest.mark.parametrize(
    "counts,exceeded",
    [
        ([20, 8], False),
        ([21, 1], True),
        ([1, 9], True),
        ([21, 9], True),
    ],
)
def test_default_limits(context, counts, exceeded):
    context.redis.eval.return_value = counts
    if exceeded:
        with pytest.raises(limiter.CafeLoginRateExceeded):
            context.charge()
    else:
        assert context.charge() is None


def test_custom_limits(context):
    context.app.config.update(
        CAFE_LOGIN_IP_LIMIT=3,
        CAFE_LOGIN_ACCOUNT_LIMIT=2,
        CAFE_LOGIN_WINDOW_SECONDS=90,
    )
    context.redis.eval.return_value = [3, 2]
    assert context.charge() is None
    assert context.redis.eval.call_args.args[4] == 90
    context.redis.eval.return_value = [3, 3]
    with pytest.raises(limiter.CafeLoginRateExceeded):
        context.charge()


@pytest.mark.parametrize(
    "key,value",
    [
        ("CAFE_LOGIN_IP_LIMIT", 0),
        ("CAFE_LOGIN_IP_LIMIT", True),
        ("CAFE_LOGIN_IP_LIMIT", 1001),
        ("CAFE_LOGIN_ACCOUNT_LIMIT", -1),
        ("CAFE_LOGIN_ACCOUNT_LIMIT", "8"),
        ("CAFE_LOGIN_WINDOW_SECONDS", 0),
        ("CAFE_LOGIN_WINDOW_SECONDS", 86401),
    ],
)
def test_bad_configuration_fails_closed(context, key, value):
    context.app.config[key] = value
    with pytest.raises(limiter.CafeLoginLimitUnavailable):
        context.charge()
    context.redis.eval.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        None, 2, "1,1", [], [1], [1, 1, 1],
        [0, 1], [1, -1], [True, 1], ["1", 1],
    ],
)
def test_invalid_redis_result_fails_closed(context, value):
    context.redis.eval.return_value = value
    with pytest.raises(limiter.CafeLoginLimitUnavailable):
        context.charge()


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("offline"),
        ConnectionError("offline"),
        TimeoutError("offline"),
        RuntimeError("offline"),
    ],
)
def test_redis_failure_fails_closed(context, error):
    context.redis.eval.side_effect = error
    with pytest.raises(limiter.CafeLoginLimitUnavailable):
        context.charge()


def test_client_acquisition_failure(context):
    context.store.get_client.side_effect = RuntimeError("offline")
    with pytest.raises(limiter.CafeLoginLimitUnavailable):
        context.charge()


def test_invalid_address_fails_before_redis(context):
    with pytest.raises(limiter.CafeLoginLimitUnavailable):
        context.charge(ip="not-an-ip")
    context.store.get_client.assert_not_called()


@pytest.mark.parametrize(
    "email", [None, 123, "", " ", "x" * 300]
)
def test_invalid_email_uses_hashed_bucket(context, email):
    assert context.charge(email=email) is None
    key = context.redis.eval.call_args.args[3]
    assert key.startswith("xuoroni:cafe:login:account:")
    assert len(key.rsplit(":", 1)[-1]) == 64
    assert not str(email) or str(email) not in key


def test_lua_script_and_namespace(context):
    context.charge()
    args = context.redis.eval.call_args.args
    assert all(
        key.startswith("xuoroni:cafe:login:")
        for key in args[2:4]
    )
    script = limiter._COUNTER_SCRIPT
    assert script.count("redis.call('INCR'") == 2
    assert script.count("redis.call('EXPIRE'") == 2
    assert "KEYS[1]" in script
    assert "KEYS[2]" in script
    assert "ARGV[1]" in script
