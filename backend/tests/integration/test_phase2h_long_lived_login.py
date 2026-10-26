"""Persistent login expiry-policy acceptance."""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from app.services.auth_service import _refresh_expiry


def make_app(days=None):
    app = Flask(__name__)
    if days is not None:
        app.config["PERSISTENT_LOGIN_REFRESH_DAYS"] = days
    return app


def test_long_lived_mobile_session_default():
    app = make_app()
    with app.app_context():
        remaining = _refresh_expiry() - datetime.now(timezone.utc)
        assert remaining > timedelta(days=3600)
        assert remaining <= timedelta(days=3650, minutes=1)


def test_policy_can_be_reduced_by_configuration():
    app = make_app(90)
    with app.app_context():
        remaining = _refresh_expiry() - datetime.now(timezone.utc)
        assert timedelta(days=89) < remaining < timedelta(days=91)


@pytest.mark.parametrize("value", [0, -1, 3651, True, "3650"])
def test_invalid_persistent_login_policy_rejected(value):
    app = make_app(value)
    with app.app_context():
        with pytest.raises(ValueError):
            _refresh_expiry()
