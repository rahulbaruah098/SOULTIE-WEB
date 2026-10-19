"""Durable Notification delivery executor.

MongoDB is the source of truth. The caller supplies a Flask app
context. This module never enqueues Celery or starts a worker.
"""

from datetime import datetime, timedelta, timezone
import os
from hashlib import sha256

from app.schemas.notification import NOTIFICATION_COPY
from app.services.firebase_push_service import (
    FirebasePushError,
    FirebasePushService,
)

from flask import current_app

from app.extensions import mongo
from app.repositories.notification_delivery_repository import (
    claim_next_due_notification_delivery,
    mark_notification_delivery_delivered,
    mark_notification_delivery_failed,
    mark_notification_delivery_suppressed,
    reschedule_notification_delivery,
)
from app.repositories.notification_preferences_repository import (
    get_notification_preferences_for_user,
)
from app.schemas.notification_preferences import (
    notification_channel_is_enabled,
)
from app.services.notification_email_policy import (
    NotificationEmailSuppressed,
    prepare_notification_email,
)
from app.services.email_sender import (
    EmailConfigurationError,
    EmailDeliveryError,
    send_email,
)


MAX_DELIVERY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 1800
DELIVERY_LEASE_SECONDS = 60


def retry_delay_seconds(attempt_count):
    if isinstance(attempt_count, bool):
        raise ValueError("Invalid delivery attempt count.")
    if not isinstance(attempt_count, int) or attempt_count < 1:
        raise ValueError("Invalid delivery attempt count.")

    return min(
        RETRY_BASE_SECONDS * (2 ** min(attempt_count - 1, 10)),
        RETRY_MAX_SECONDS,
    )


def _timestamp(now):
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Invalid executor timestamp.")
    return now.astimezone(timezone.utc)


def _transition(claim, operation, **kwargs):
    updated = operation(
        claim["_id"],
        expected_attempt_count=claim["attempt_count"],
        **kwargs,
    )
    return "updated" if updated is not None else "stale"


def _retry_or_fail(claim, *, code, now):
    attempt = claim["attempt_count"]

    if attempt >= MAX_DELIVERY_ATTEMPTS:
        result = _transition(
            claim,
            mark_notification_delivery_failed,
            error_code=code,
            now=now,
        )
        return "failed" if result == "updated" else "stale"

    retry_at = now + timedelta(
        seconds=retry_delay_seconds(attempt)
    )

    result = _transition(
        claim,
        reschedule_notification_delivery,
        next_attempt_at=retry_at,
        error_code=code,
        now=now,
    )

    return "pending" if result == "updated" else "stale"


def execute_next_notification_delivery(*, now=None):
    """Claim and execute at most one due durable delivery.

    SMTP acknowledgments are not distributed transactions.
    A worker crash after SMTP acceptance but before MongoDB update
    may lead to an at-least-once duplicate email on retry.
    """
    timestamp = _timestamp(now)

    claim = claim_next_due_notification_delivery(
        now=timestamp,
        lease_seconds=DELIVERY_LEASE_SECONDS,
    )

    if claim is None:
        return {"status": "idle"}

    if claim["channel"] not in {"email", "push"}:
        status = _transition(
            claim,
            mark_notification_delivery_suppressed,
            error_code="UNSUPPORTED_CHANNEL",
            now=timestamp,
        )
        return {"status": "suppressed" if status == "updated" else "stale"}

    notification = mongo.db.notifications.find_one({
        "_id": claim["notification_id"],
        "user_id": claim["user_id"],
    })

    if notification is None:
        status = _transition(
            claim,
            mark_notification_delivery_suppressed,
            error_code="NOTIFICATION_UNAVAILABLE",
            now=timestamp,
        )
        return {"status": "suppressed" if status == "updated" else "stale"}

    preferences = get_notification_preferences_for_user(
        claim["user_id"]
    )

    if not notification_channel_is_enabled(
        preferences,
        claim["channel"],
    ):
        status = _transition(
            claim,
            mark_notification_delivery_suppressed,
            error_code="CHANNEL_DISABLED",
            now=timestamp,
        )
        return {"status": "suppressed" if status == "updated" else "stale"}

    if claim["channel"] == "push":
        if os.getenv("XUORONI_FCM_PUSH_DELIVERY_ENABLED") != "true":
            return {
                "status": _retry_or_fail(
                    claim,
                    code="PUSH_PROVIDER_UNAVAILABLE",
                    now=timestamp,
                )
            }

        kind = notification.get("type")
        if kind not in NOTIFICATION_COPY:
            status = _transition(
                claim,
                mark_notification_delivery_suppressed,
                error_code="PUSH_POLICY_INVALID",
                now=timestamp,
            )
            return {
                "status": (
                    "suppressed" if status == "updated" else "stale"
                )
            }

        if datetime.now(timezone.utc) >= claim["lease_expires_at"]:
            return {"status": "lease_expired"}

        devices = list(
            mongo.db.user_devices.find(
                {
                    "user_id": claim["user_id"],
                    "push_provider": "fcm",
                    "push_enabled": True,
                    "revoked_at": None,
                    "push_token": {"$type": "string"},
                },
                {"push_token": 1, "device_id": 1},
            ).limit(21)
        )

        if not devices:
            status = _transition(
                claim,
                mark_notification_delivery_suppressed,
                error_code="NO_ACTIVE_PUSH_DEVICE",
                now=timestamp,
            )
            return {
                "status": (
                    "suppressed" if status == "updated" else "stale"
                )
            }

        # Bounded sending. A larger fanout needs a durable
        # per-device delivery model before activation.
        if len(devices) > 20:
            return {
                "status": _retry_or_fail(
                    claim,
                    code="PUSH_DEVICE_LIMIT_EXCEEDED",
                    now=timestamp,
                )
            }

        try:
            provider = FirebasePushService()
            title, body = NOTIFICATION_COPY[kind]

            # Durable per-token checkpoints prevent resending to
            # already-recorded devices after partial fanout failure.
            # They cannot eliminate an FCM-accepted-before-DB-write
            # crash window: FCM itself is not exactly-once.
            accepted = set(claim.get("push_accepted_devices") or [])

            for device in sorted(
                devices, key=lambda item: item["device_id"]
            ):
                current_time = datetime.now(timezone.utc)
                if current_time >= claim["lease_expires_at"]:
                    return {"status": "lease_expired"}

                fingerprint = sha256(
                    device["push_token"].encode("utf-8")
                ).hexdigest()

                if fingerprint in accepted:
                    continue

                # Confirm that this worker still holds its claim
                # before making the external provider request.
                active = mongo.db.notification_deliveries.find_one(
                    {
                        "_id": claim["_id"],
                        "status": "processing",
                        "attempt_count": claim["attempt_count"],
                        "lease_expires_at": {"$gt": current_time},
                    },
                    {"_id": 1},
                )
                if active is None:
                    return {"status": "stale"}

                provider.send(
                    token=device["push_token"],
                    title=title,
                    body=body,
                    data={
                        "notification_id": str(notification["_id"]),
                        "type": kind,
                    },
                    enabled=True,
                )

                # Record each accepted token before sending to
                # another device. Preserve checkpoints on retries.
                checkpoint_time = datetime.now(timezone.utc)

                receipt = mongo.db.notification_deliveries.update_one(
                    {
                        "_id": claim["_id"],
                        "status": "processing",
                        "attempt_count": claim["attempt_count"],
                        "lease_expires_at": {"$gt": checkpoint_time},
                    },
                    {
                        "$addToSet": {
                            "push_accepted_devices": fingerprint,
                        },
                        "$set": {
                            "updated_at": checkpoint_time,
                        },
                    },
                )

                if receipt.matched_count != 1:
                    return {"status": "stale"}

                accepted.add(fingerprint)

        except FirebasePushError as exc:
            if exc.code == "FCM_DEVICE_UNREGISTERED":
                # Only revoke the exact token that FCM rejected.
                # A token rotated concurrently must remain active.
                mongo.db.user_devices.update_one(
                    {
                        "user_id": claim["user_id"],
                        "device_id": device["device_id"],
                        "push_provider": "fcm",
                        "push_token": device["push_token"],
                        "push_enabled": True,
                        "revoked_at": None,
                    },
                    {
                        "$set": {
                            "push_enabled": False,
                            "revoked_at": datetime.now(timezone.utc),
                            "updated_at": datetime.now(timezone.utc),
                        },
                        "$unset": {
                            "push_token": "",
                            "push_token_hash": "",
                        },
                    },
                )
            return {
                "status": _retry_or_fail(
                    claim,
                    code=exc.code,
                    now=timestamp,
                )
            }

        status = _transition(
            claim,
            mark_notification_delivery_delivered,
            now=datetime.now(timezone.utc),
        )
        return {
            "status": (
                "delivered" if status == "updated" else "stale"
            )
        }

    try:
        prepared = prepare_notification_email(notification)

    except NotificationEmailSuppressed as exc:
        status = _transition(
            claim,
            mark_notification_delivery_suppressed,
            error_code=exc.code,
            now=timestamp,
        )
        return {"status": "suppressed" if status == "updated" else "stale"}

    except ValueError:
        status = _transition(
            claim,
            mark_notification_delivery_suppressed,
            error_code="EMAIL_POLICY_INVALID",
            now=timestamp,
        )
        return {"status": "suppressed" if status == "updated" else "stale"}

    # Guard against an already-expired claim before external I/O.
    if datetime.now(timezone.utc) >= claim["lease_expires_at"]:
        return {"status": "lease_expired"}

    try:
        accepted = send_email(**prepared)
        if accepted is not True:
            raise EmailDeliveryError("SMTP did not confirm acceptance.")

    except EmailConfigurationError:
        return {
            "status": _retry_or_fail(
                claim, code="EMAIL_NOT_CONFIGURED", now=timestamp
            )
        }

    except EmailDeliveryError:
        return {
            "status": _retry_or_fail(
                claim, code="EMAIL_DELIVERY_FAILED", now=timestamp
            )
        }

    status = _transition(
        claim,
        mark_notification_delivery_delivered,
        now=datetime.now(timezone.utc),
    )

    return {"status": "delivered" if status == "updated" else "stale"}


__all__ = [
    "MAX_DELIVERY_ATTEMPTS",
    "retry_delay_seconds",
    "execute_next_notification_delivery",
]
