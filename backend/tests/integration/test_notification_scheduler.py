"""Scheduled Notification delivery and cursor regression tests."""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from flask import Flask

import app.jobs.notification_tasks as tasks
from app.jobs.celery_app import (
    celery_app,
    create_notification_celery,
)


def _app(enabled):
    app = Flask(__name__)
    app.config["CELERY_BROKER_URL"] = "memory://"
    app.config["NOTIFICATION_CELERY_BEAT_ENABLED"] = enabled
    return app


def test_default_scheduling_is_off():
    assert celery_app.conf.beat_schedule == {}


def test_explicit_false_does_not_schedule():
    worker = create_notification_celery(_app(False))
    assert worker.conf.beat_schedule == {}


def test_enabled_schedules_bounded_tasks():
    worker = create_notification_celery(_app(True))
    schedule = worker.conf.beat_schedule

    assert len(schedule) == 2
    assert schedule["xuoroni-notifications-process-due"]["args"] == (1,)
    assert schedule["xuoroni-notifications-process-due"]["schedule"] == 60.0
    assert schedule["xuoroni-notifications-reconcile"]["schedule"] == 300.0


def test_string_configuration_cannot_accidentally_enable():
    worker = create_notification_celery(_app("true"))
    assert worker.conf.beat_schedule == {}


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


class StateCollection:
    def __init__(self, *, busy=False, cursor=None):
        self.busy = busy
        self.cursor = cursor or {}
        self.updates = []

    def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return Result()

    def find_one_and_update(self, query, update, **kwargs):
        if self.busy:
            return None

        return {
            "cursor_created_at": self.cursor.get("created_at"),
            "cursor_notification_id": self.cursor.get(
                "notification_id"
            ),
        }


def _setup(monkeypatch, collection, reconcile):
    class DB:
        notification_reconciliation_state = collection

    class Mongo:
        db = DB()

    monkeypatch.setattr(tasks, "mongo", Mongo())
    monkeypatch.setattr(
        tasks,
        "reconcile_notification_deliveries",
        reconcile,
    )


def test_busy_lease_does_not_scan(monkeypatch):
    collection = StateCollection(busy=True)

    def forbidden(**kwargs):
        raise AssertionError("Busy scan must not execute")

    _setup(monkeypatch, collection, forbidden)

    result = tasks.reconcile_notification_delivery_page.run()

    assert result == {"status": "busy", "scanned": 0}


def test_first_page_persists_cursor(monkeypatch):
    collection = StateCollection()
    cursor = {
        "created_at": datetime.now(timezone.utc),
        "notification_id": ObjectId(),
    }

    calls = []

    def reconcile(**kwargs):
        calls.append(kwargs)
        return {
            "scanned": 25,
            "has_more": True,
            "next_cursor": cursor,
        }

    _setup(monkeypatch, collection, reconcile)

    result = tasks.reconcile_notification_delivery_page.run()

    assert result["status"] == "continued"
    assert calls[0]["limit"] == 25
    assert calls[0]["after_created_at"] is None

    saved = collection.updates[-1][1]["$set"]
    assert saved["cursor_created_at"] == cursor["created_at"]
    assert saved["cursor_notification_id"] == cursor["notification_id"]


def test_existing_cursor_is_forwarded(monkeypatch):
    cursor = {
        "created_at": datetime.now(timezone.utc),
        "notification_id": ObjectId(),
    }

    collection = StateCollection(cursor=cursor)
    calls = []

    def reconcile(**kwargs):
        calls.append(kwargs)
        return {
            "scanned": 4,
            "has_more": False,
            "next_cursor": None,
        }

    _setup(monkeypatch, collection, reconcile)

    result = tasks.reconcile_notification_delivery_page.run()

    assert result["status"] == "completed"
    assert calls[0]["after_created_at"] == cursor["created_at"]
    assert calls[0]["after_notification_id"] == cursor["notification_id"]

    saved = collection.updates[-1][1]["$set"]
    assert saved["cursor_created_at"] is None
    assert saved["cursor_notification_id"] is None


def test_failed_page_preserves_cursor(monkeypatch):
    cursor = {
        "created_at": datetime.now(timezone.utc),
        "notification_id": ObjectId(),
    }
    collection = StateCollection(cursor=cursor)

    def reconcile(**kwargs):
        raise RuntimeError("Database unavailable")

    _setup(monkeypatch, collection, reconcile)

    with pytest.raises(RuntimeError, match="Database unavailable"):
        tasks.reconcile_notification_delivery_page.run()

    saved = collection.updates[-1][1]["$set"]
    assert "cursor_created_at" not in saved
    assert "cursor_notification_id" not in saved
    assert saved["lease_until"] is None


def test_invalid_cursor_fails_closed(monkeypatch):
    collection = StateCollection()

    _setup(
        monkeypatch,
        collection,
        lambda **kwargs: {
            "scanned": 25,
            "has_more": True,
            "next_cursor": None,
        },
    )

    with pytest.raises(RuntimeError, match="Missing reconciliation cursor"):
        tasks.reconcile_notification_delivery_page.run()


def test_reconciliation_does_not_send(monkeypatch):
    collection = StateCollection()

    _setup(
        monkeypatch,
        collection,
        lambda **kwargs: {
            "scanned": 0,
            "has_more": False,
            "next_cursor": None,
        },
    )

    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        lambda: (_ for _ in ()).throw(
            AssertionError("Reconciliation must not send")
        ),
    )

    assert (
        tasks.reconcile_notification_delivery_page.run()["status"]
        == "completed"
    )


def test_reconciliation_task_registered():
    assert (
        "xuoroni.notifications.reconcile_page"
        in celery_app.tasks
    )
