"""Isolated cafe registration rate-limiting tests."""

from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.security import cafe_registration_limit as limits


@pytest.fixture
def limit_context(monkeypatch):
    app = Flask("xuoroni-cafe-registration-limit-test")
    app.config["TESTING"] = True

    counter = {"value": 0}

    def evaluate(script, number_of_keys, key, window):
        assert number_of_keys == 1
        assert "INCR" in script
        assert "EXPIRE" in script
        assert window == 3600
        assert key.startswith("xuoroni:cafe:register:ip:")
        counter["value"] += 1
        return counter["value"]

    client = Mock()
    client.eval.side_effect = evaluate
    store = SimpleNamespace(get_client=lambda: client)

    monkeypatch.setattr(limits, "redis_store", store)

    return SimpleNamespace(
        app=app,
        redis=client,
        counter=counter,
    )


def invoke(context, *, ip="192.0.2.10", headers=None):
    with context.app.test_request_context(
        "/api/v1/cafe/register",
        method="POST",
        environ_base={"REMOTE_ADDR": ip} if ip is not None else {},
        headers=headers or {},
    ):
        return limits.check_cafe_registration_limit()


def test_first_ten_requests_allowed(limit_context):
    ctx = limit_context

    for _ in range(10):
        assert invoke(ctx) is None

    assert ctx.counter["value"] == 10
    assert ctx.redis.eval.call_count == 10


def test_eleventh_request_rejected(limit_context):
    ctx = limit_context

    for _ in range(10):
        invoke(ctx)

    with pytest.raises(limits.CafeRegistrationRateExceeded):
        invoke(ctx)

    assert ctx.redis.eval.call_count == 11


def test_ip_address_not_stored_in_plaintext(limit_context):
    ctx = limit_context
    invoke(ctx)

    arguments = ctx.redis.eval.call_args.args
    redis_key = arguments[2]

    assert "192.0.2.10" not in redis_key
    assert len(redis_key.split(":")[-1]) == 64


def test_forwarded_header_not_trusted(limit_context):
    ctx = limit_context

    invoke(
        ctx,
        ip="192.0.2.10",
        headers={"X-Forwarded-For": "198.51.100.99"},
    )
    first_key = ctx.redis.eval.call_args.args[2]

    invoke(ctx, ip="192.0.2.10")
    second_key = ctx.redis.eval.call_args.args[2]

    assert first_key == second_key


def test_ipv6_supported(limit_context):
    ctx = limit_context
    assert invoke(ctx, ip="2001:db8::1") is None

    key = ctx.redis.eval.call_args.args[2]
    assert "2001:db8" not in key


@pytest.mark.parametrize(
    "invalid_ip",
    ["not-an-ip", "192.0.2.999", "", "192.0.2.1, 192.0.2.2"],
)
def test_invalid_ip_fails_closed(limit_context, invalid_ip):
    ctx = limit_context

    with pytest.raises(limits.CafeRegistrationLimitError):
        invoke(ctx, ip=invalid_ip)

    ctx.redis.eval.assert_not_called()


@pytest.mark.parametrize(
    "configuration",
    [
        {"CAFE_REGISTRATION_LIMIT": 0},
        {"CAFE_REGISTRATION_LIMIT": 101},
        {"CAFE_REGISTRATION_LIMIT": True},
        {"CAFE_REGISTRATION_LIMIT": "10"},
        {"CAFE_REGISTRATION_WINDOW_SECONDS": 0},
        {"CAFE_REGISTRATION_WINDOW_SECONDS": 86401},
        {"CAFE_REGISTRATION_WINDOW_SECONDS": False},
    ],
)
def test_invalid_configuration_fails_closed(
    limit_context, configuration
):
    ctx = limit_context
    ctx.app.config.update(configuration)

    with pytest.raises(limits.CafeRegistrationLimitError):
        invoke(ctx)

    ctx.redis.eval.assert_not_called()


def test_redis_connection_failure_fails_closed(limit_context):
    ctx = limit_context
    ctx.redis.eval.side_effect = RedisConnectionError(
        "Simulated Redis outage"
    )

    with pytest.raises(limits.CafeRegistrationLimitError) as error:
        invoke(ctx)

    assert not isinstance(
        error.value,
        limits.CafeRegistrationRateExceeded,
    )


def test_redis_counter_malformed_fails_closed(limit_context):
    ctx = limit_context
    ctx.redis.eval.side_effect = None
    ctx.redis.eval.return_value = "1"

    with pytest.raises(limits.CafeRegistrationLimitError):
        invoke(ctx)


def test_redis_boolean_counter_fails_closed(limit_context):
    ctx = limit_context
    ctx.redis.eval.side_effect = None
    ctx.redis.eval.return_value = True

    with pytest.raises(limits.CafeRegistrationLimitError):
        invoke(ctx)


def test_missing_redis_client_fails_closed(limit_context, monkeypatch):
    ctx = limit_context

    def unavailable():
        raise RuntimeError("Redis is not initialized")

    monkeypatch.setattr(
        limits,
        "redis_store",
        SimpleNamespace(get_client=unavailable),
    )

    with pytest.raises(limits.CafeRegistrationLimitError):
        invoke(ctx)


def test_request_limit_configuration_respected(
    limit_context, monkeypatch
):
    ctx = limit_context
    ctx.app.config["CAFE_REGISTRATION_LIMIT"] = 2

    assert invoke(ctx) is None
    assert invoke(ctx) is None

    with pytest.raises(limits.CafeRegistrationRateExceeded):
        invoke(ctx)


def test_redis_key_uses_canonical_ip_representation(limit_context):
    ctx = limit_context

    assert ip_address("2001:0db8:0:0:0:0:0:1") == ip_address(
        "2001:db8::1"
    )

    invoke(ctx, ip="2001:0db8:0:0:0:0:0:1")
    first_key = ctx.redis.eval.call_args.args[2]

    invoke(ctx, ip="2001:db8::1")
    second_key = ctx.redis.eval.call_args.args[2]

    assert first_key == second_key
