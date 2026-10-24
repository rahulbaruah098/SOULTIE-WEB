"""Explicitly enabled, bounded Safe Meet notification runner.

Run from a separately supervised backend worker process.
Never start background workers inside Flask application initialization.
"""

import os
from uuid import uuid4

from app.services.safe_meet_fcm_transport import (
    send_safe_meet_fcm,
)
from app.services.safe_meet_notification_push_adapter import (
    deliver_safe_meet_push,
)
from app.services.safe_meet_notification_worker_service import (
    run_one_safe_meet_notification,
)


def run_safe_meet_notification_batch(
    *,
    database,
    enabled=False,
    max_jobs=25,
    worker_id=None,
    transport=None,
    device_loader=None,
):
    if enabled is not True:
        raise RuntimeError(
            "Safe Meet notification sending is disabled."
        )
    if type(max_jobs) is not int or not 1 <= max_jobs <= 500:
        raise ValueError("Invalid batch size.")

    transport = transport or send_safe_meet_fcm
    worker_id = worker_id or ("safe-meet-" + uuid4().hex)

    outcomes = []
    for _ in range(max_jobs):
        def sender(item):
            return deliver_safe_meet_push(
                item,
                transport=transport,
                device_loader=device_loader,
            )

        result = run_one_safe_meet_notification(
            database=database,
            worker_id=worker_id,
            sender=sender,
        )
        outcomes.append(result["outcome"])
        if result["outcome"] == "idle":
            break

    return {
        "worker_id": worker_id,
        "processed": sum(x != "idle" for x in outcomes),
        "sent": outcomes.count("sent"),
        "retry_pending": outcomes.count("pending"),
        "failed": outcomes.count("failed"),
        "lease_lost": outcomes.count("lease_lost"),
    }
