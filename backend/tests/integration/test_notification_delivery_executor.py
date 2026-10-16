"""Permanent isolated Notification executor contract tests."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask

import app.services.notification_delivery_executor as executor


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.config["TESTING"] = True
    return application


def _claim(channel="email", attempt=1):
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "user_id": ObjectId(),
        "notification_id": ObjectId(),
        "channel": channel,
        "attempt_count": attempt,
        "lease_expires_at": now + timedelta(seconds=60),
    }


def _setup(monkeypatch, claim, *, enabled=True, exists=True):
    monkeypatch.setattr(
        executor,
        "claim_next_due_notification_delivery",
        lambda **kwargs: claim,
    )

    class Notifications:
        def find_one(self, query):
            if not exists:
                return None
            return {
                "_id": claim["notification_id"],
                "user_id": claim["user_id"],
                "type": "new_message",
            }

    class Database:
        notifications = Notifications()

    class Mongo:
        db = Database()

    monkeypatch.setattr(executor, "mongo", Mongo())
    monkeypatch.setattr(
        executor,
        "get_notification_preferences_for_user",
        lambda user_id: {},
    )
    monkeypatch.setattr(
        executor,
        "notification_channel_is_enabled",
        lambda prefs, channel: enabled,
    )


def _capture(monkeypatch, name, result=None):
    calls = []

    def transition(*args, **kwargs):
        calls.append((args, kwargs))
        return result if result is not None else {"status": "ok"}

    monkeypatch.setattr(executor, name, transition)
    return calls


def test_idle_claim(app, monkeypatch):
    monkeypatch.setattr(
        executor,
        "claim_next_due_notification_delivery",
        lambda **kwargs: None,
    )
    with app.app_context():
        assert executor.execute_next_notification_delivery() == {
            "status": "idle"
        }


def test_missing_notification_suppressed(app, monkeypatch):
    claim = _claim()
    _setup(monkeypatch, claim, exists=False)
    calls = _capture(
        monkeypatch, "mark_notification_delivery_suppressed"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "suppressed"
    assert calls[0][1]["error_code"] == "NOTIFICATION_UNAVAILABLE"
    assert calls[0][1]["expected_attempt_count"] == 1


def test_disabled_channel_suppressed(app, monkeypatch):
    claim = _claim()
    _setup(monkeypatch, claim, enabled=False)
    calls = _capture(
        monkeypatch, "mark_notification_delivery_suppressed"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "suppressed"
    assert calls[0][1]["error_code"] == "CHANNEL_DISABLED"


def test_push_provider_unavailable_is_retried(app, monkeypatch):
    claim = _claim(channel="push")
    _setup(monkeypatch, claim)
    calls = _capture(
        monkeypatch, "reschedule_notification_delivery"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "pending"
    assert calls[0][1]["error_code"] == "PUSH_PROVIDER_UNAVAILABLE"
    assert calls[0][1]["expected_attempt_count"] == 1


def test_email_success_marks_delivered(app, monkeypatch):
    claim = _claim()
    _setup(monkeypatch, claim)

    monkeypatch.setattr(
        executor,
        "prepare_notification_email",
        lambda notification: {
            "recipient": "verified@example.com",
            "subject": "Xuoroni: New message",
            "text_body": "Open Xuoroni.",
        },
    )

    sent = []
    monkeypatch.setattr(
        executor,
        "send_email",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    calls = _capture(
        monkeypatch, "mark_notification_delivery_delivered"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "delivered"
    assert len(sent) == 1
    assert sent[0]["recipient"] == "verified@example.com"
    assert calls[0][1]["expected_attempt_count"] == 1


def test_no_verified_email_suppressed(app, monkeypatch):
    claim = _claim()
    _setup(monkeypatch, claim)

    def unavailable(notification):
        raise executor.NotificationEmailSuppressed(
            "NO_VERIFIED_EMAIL"
        )

    monkeypatch.setattr(
        executor, "prepare_notification_email", unavailable
    )
    calls = _capture(
        monkeypatch, "mark_notification_delivery_suppressed"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "suppressed"
    assert calls[0][1]["error_code"] == "NO_VERIFIED_EMAIL"


def test_smtp_failure_retries(app, monkeypatch):
    claim = _claim()
    _setup(monkeypatch, claim)

    monkeypatch.setattr(
        executor, "prepare_notification_email",
        lambda notification: {
            "recipient": "verified@example.com",
            "subject": "Test",
            "text_body": "Test",
        },
    )

    def fail(**kwargs):
        raise executor.EmailDeliveryError("SMTP failure")

    monkeypatch.setattr(executor, "send_email", fail)

    calls = _capture(
        monkeypatch, "reschedule_notification_delivery"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "pending"
    assert calls[0][1]["error_code"] == "EMAIL_DELIVERY_FAILED"


def test_terminal_attempt_fails(app, monkeypatch):
    claim = _claim(channel="push", attempt=5)
    _setup(monkeypatch, claim)

    calls = _capture(
        monkeypatch, "mark_notification_delivery_failed"
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "failed"
    assert calls[0][1]["expected_attempt_count"] == 5


def test_stale_transition_is_not_success(app, monkeypatch):
    claim = _claim(channel="push")
    _setup(monkeypatch, claim)

    monkeypatch.setattr(
        executor,
        "reschedule_notification_delivery",
        lambda *args, **kwargs: None,
    )

    with app.app_context():
        result = executor.execute_next_notification_delivery()

    assert result["status"] == "stale"


@pytest.mark.parametrize(
    "value", [0, -1, True, False, None, "2"]
)
def test_invalid_retry_generation(value):
    with pytest.raises(ValueError):
        executor.retry_delay_seconds(value)


def test_retry_backoff_is_bounded():
    assert executor.retry_delay_seconds(1) == 30
    assert executor.retry_delay_seconds(2) == 60
    assert executor.retry_delay_seconds(3) == 120
    assert executor.retry_delay_seconds(50) == 1800
