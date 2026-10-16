"""Celery Notification worker contract regression tests."""

import pytest

import app.jobs.notification_tasks as tasks
from app.jobs.celery_app import celery_app


def test_scheduling_disabled():
    assert celery_app.conf.beat_schedule == {}
    assert celery_app.conf.task_always_eager is False
    assert celery_app.conf.task_ignore_result is True


def test_worker_safety_configuration():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_worker_time_limit_shorter_than_lease():
    from app.services.notification_delivery_executor import (
        DELIVERY_LEASE_SECONDS,
    )

    assert celery_app.conf.task_time_limit < DELIVERY_LEASE_SECONDS


@pytest.mark.parametrize(
    "invalid",
    [None, 0, 2, 3, -1, True, False, "1", 1.5],
)
def test_invalid_batch_sizes(invalid):
    with pytest.raises(ValueError):
        tasks.process_due_notification_deliveries.run(invalid)


def test_empty_queue(monkeypatch):
    calls = []

    def recover():
        calls.append("recover")
        return 2

    def execute():
        calls.append("execute")
        return {"status": "idle"}

    monkeypatch.setattr(
        tasks,
        "recover_expired_notification_delivery_leases",
        recover,
    )
    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        execute,
    )

    assert tasks.process_due_notification_deliveries.run(1) == {
        "recovered": 2,
        "processed": 0,
        "statuses": [],
    }

    assert calls == ["recover", "execute"]


def test_bounded_processing(monkeypatch):
    calls = []

    monkeypatch.setattr(
        tasks,
        "recover_expired_notification_delivery_leases",
        lambda: 0,
    )

    def execute():
        calls.append(1)
        return {"status": "pending"}

    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        execute,
    )

    output = tasks.process_due_notification_deliveries.run(1)

    assert output["processed"] == 1
    assert output["statuses"] == ["pending"]
    assert len(calls) == 1


def test_unknown_status_rejected(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "recover_expired_notification_delivery_leases",
        lambda: 0,
    )

    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        lambda: {"status": "invalid"},
    )

    with pytest.raises(RuntimeError):
        tasks.process_due_notification_deliveries.run(1)


def test_recovery_task_does_not_send(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "recover_expired_notification_delivery_leases",
        lambda: 4,
    )

    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        lambda: (_ for _ in ()).throw(
            AssertionError("Delivery must not execute")
        ),
    )

    assert tasks.recover_notification_leases.run() == {
        "recovered": 4
    }


def test_celery_tasks_registered():
    assert "xuoroni.notifications.process_due" in celery_app.tasks
    assert "xuoroni.notifications.recover_leases" in celery_app.tasks

def test_single_delivery_batch_limit():
    assert tasks.MAX_DELIVERY_BATCH_SIZE == 1
    assert tasks._batch_size(1) == 1

    with pytest.raises(ValueError):
        tasks._batch_size(2)


def test_worker_timeout_budget():
    from app.services.notification_delivery_executor import (
        DELIVERY_LEASE_SECONDS,
    )

    assert celery_app.conf.task_soft_time_limit == 35
    assert celery_app.conf.task_time_limit == 40
    assert celery_app.conf.task_time_limit < DELIVERY_LEASE_SECONDS


def test_single_task_executes_only_once(monkeypatch):
    events = []

    def recover():
        events.append("recover")
        return 0

    def execute():
        events.append("deliver")
        return {"status": "delivered"}

    monkeypatch.setattr(
        tasks,
        "recover_expired_notification_delivery_leases",
        recover,
    )
    monkeypatch.setattr(
        tasks,
        "execute_next_notification_delivery",
        execute,
    )

    result = tasks.process_due_notification_deliveries.run(1)

    assert result == {
        "recovered": 0,
        "processed": 1,
        "statuses": ["delivered"],
    }

    assert events == ["recover", "deliver"]
