"""Durable MongoDB queue state for Xuoroni Notification delivery."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.schemas.notification_delivery import (
    build_notification_delivery_document,
    normalize_notification_delivery_channel,
    normalize_notification_delivery_error_code,
    validate_notification_delivery_document,
)


DEFAULT_NOTIFICATION_DELIVERY_LEASE_SECONDS = 60

MAX_NOTIFICATION_DELIVERY_LEASE_SECONDS = 900

WORKER_LEASE_EXPIRED_ERROR_CODE = (
    "WORKER_LEASE_EXPIRED"
)


def utc_now():
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
    *,
    field_name,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(
                value
            )
        )

    except (
        InvalidId,
        TypeError,
        ValueError,
    ):
        raise ValueError(
            f"Invalid {field_name}."
        ) from None


def _normalize_timestamp(
    value,
    field_name,
):
    if not isinstance(
        value,
        datetime,
    ):
        raise ValueError(
            f"{field_name} must be a datetime."
        )

    if value.tzinfo is None:
        raise ValueError(
            f"{field_name} must be timezone-aware."
        )

    return value.astimezone(
        timezone.utc
    )


def _normalize_now(
    now,
):
    if now is None:
        return utc_now()

    return _normalize_timestamp(
        now,
        "notification delivery time",
    )


def _normalize_lease_seconds(
    value,
):
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
    ):
        raise ValueError(
            "Notification delivery lease seconds "
            "must be an integer."
        )

    if value <= 0:
        raise ValueError(
            "Notification delivery lease seconds "
            "must be positive."
        )

    if (
        value
        > MAX_NOTIFICATION_DELIVERY_LEASE_SECONDS
    ):
        raise ValueError(
            "Notification delivery lease seconds "
            "exceed the maximum."
        )

    return value


def _normalize_expected_attempt_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Invalid notification delivery attempt count.")
    return value


_MONGO_DELIVERY_DATETIME_FIELDS = (
    "next_attempt_at",
    "lease_expires_at",
    "last_attempt_at",
    "delivered_at",
    "failed_at",
    "suppressed_at",
    "created_at",
    "updated_at",
)


def _normalize_mongo_delivery_document(
    delivery,
):
    """
    Canonicalize PyMongo-returned delivery timestamps to UTC-aware values.

    MongoDB stores UTC datetimes, while PyMongo's default decoding returns
    them without tzinfo. The repository adapts that persistence representation
    before schema validation or returning a delivery to callers.
    """

    if delivery is None:
        return None

    normalized = dict(
        delivery
    )

    for field_name in (
        _MONGO_DELIVERY_DATETIME_FIELDS
    ):
        value = normalized.get(
            field_name
        )

        if (
            isinstance(
                value,
                datetime,
            )
            and value.tzinfo is None
        ):
            normalized[
                field_name
            ] = value.replace(
                tzinfo=timezone.utc
            )

    validate_notification_delivery_document(
        normalized
    )

    return normalized


def _find_by_notification_channel(
    notification_id,
    channel,
):
    return _normalize_mongo_delivery_document(
        mongo.db.notification_deliveries.find_one(
            {
                "notification_id": (
                    notification_id
                ),
                "channel": channel,
            }
        )
    )


def _idempotent_existing_result(
    existing,
    candidate,
):
    if existing is None:
        return None

    validated = (
        validate_notification_delivery_document(
            existing
        )
    )

    if (
        validated[
            "notification_id"
        ]
        != candidate[
            "notification_id"
        ]
    ):
        raise ValueError(
            "Notification delivery identity conflicts "
            "with an existing record."
        )

    if (
        validated[
            "user_id"
        ]
        != candidate[
            "user_id"
        ]
    ):
        raise ValueError(
            "Notification delivery owner conflicts "
            "with an existing record."
        )

    if (
        validated[
            "channel"
        ]
        != candidate[
            "channel"
        ]
    ):
        raise ValueError(
            "Notification delivery channel conflicts "
            "with an existing record."
        )

    return {
        "delivery": existing,
        "created": False,
    }


def create_notification_delivery_idempotent(
    *,
    notification_id,
    user_id,
    channel,
    now=None,
    next_attempt_at=None,
):
    """
    Ensure one durable delivery row for a Notification/channel pair.

    Concurrent creators converge through the unique MongoDB index.
    """

    candidate = (
        build_notification_delivery_document(
            notification_id=notification_id,
            user_id=user_id,
            channel=channel,
            now=now,
            next_attempt_at=next_attempt_at,
        )
    )

    existing = (
        _find_by_notification_channel(
            candidate[
                "notification_id"
            ],
            candidate[
                "channel"
            ],
        )
    )

    existing_result = (
        _idempotent_existing_result(
            existing,
            candidate,
        )
    )

    if existing_result is not None:
        return existing_result

    try:
        inserted = (
            mongo.db.notification_deliveries.insert_one(
                candidate
            )
        )

    except DuplicateKeyError:
        existing = (
            _find_by_notification_channel(
                candidate[
                    "notification_id"
                ],
                candidate[
                    "channel"
                ],
            )
        )

        existing_result = (
            _idempotent_existing_result(
                existing,
                candidate,
            )
        )

        if existing_result is None:
            raise

        return existing_result

    created = (
        mongo.db.notification_deliveries.find_one(
            {
                "_id": inserted.inserted_id,
            }
        )
    )

    created = (
        _normalize_mongo_delivery_document(
            created
        )
    )

    if created is None:
        raise RuntimeError(
            "Created Notification delivery "
            "could not be reloaded."
        )

    validate_notification_delivery_document(
        created
    )

    return {
        "delivery": created,
        "created": True,
    }


def get_notification_delivery_by_id(
    delivery_id,
):
    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    return _normalize_mongo_delivery_document(
        mongo.db.notification_deliveries.find_one(
            {
                "_id": delivery_object_id,
            }
        )
    )


def get_notification_delivery_by_notification_channel(
    *,
    notification_id,
    channel,
):
    notification_object_id = (
        _to_object_id(
            notification_id,
            field_name="Notification ID",
        )
    )

    normalized_channel = (
        normalize_notification_delivery_channel(
            channel
        )
    )

    return (
        _find_by_notification_channel(
            notification_object_id,
            normalized_channel,
        )
    )


def claim_next_due_notification_delivery(
    *,
    now=None,
    lease_seconds=(
        DEFAULT_NOTIFICATION_DELIVERY_LEASE_SECONDS
    ),
    channel=None,
):
    """
    Atomically claim the earliest due pending delivery.

    A temporary lease prevents concurrent workers from owning the
    same delivery. Expired leases are recovered separately.
    """

    timestamp = _normalize_now(
        now
    )

    normalized_lease_seconds = (
        _normalize_lease_seconds(
            lease_seconds
        )
    )

    lease_expires_at = (
        timestamp
        + timedelta(
            seconds=(
                normalized_lease_seconds
            )
        )
    )

    query = {
        "status": "pending",
        "next_attempt_at": {
            "$lte": timestamp,
        },
    }

    if channel is not None:
        query[
            "channel"
        ] = (
            normalize_notification_delivery_channel(
                channel
            )
        )

    claimed = (
        mongo.db.notification_deliveries.find_one_and_update(
            query,
            {
                "$set": {
                    "status": "processing",
                    "next_attempt_at": None,
                    "lease_expires_at": (
                        lease_expires_at
                    ),
                    "last_attempt_at": (
                        timestamp
                    ),
                    "updated_at": timestamp,
                    "last_error_code": None,
                },
                "$inc": {
                    "attempt_count": 1,
                },
            },
            sort=[
                (
                    "next_attempt_at",
                    1,
                ),
                (
                    "created_at",
                    1,
                ),
                (
                    "_id",
                    1,
                ),
            ],
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    claimed = (
        _normalize_mongo_delivery_document(
            claimed
        )
    )

    if claimed is None:
        return None

    return claimed


def recover_expired_notification_delivery_leases(
    *,
    now=None,
):
    """
    Requeue processing deliveries whose worker lease has expired.

    The attempt count is preserved; the next successful claim will
    increment it again.
    """

    timestamp = _normalize_now(
        now
    )

    result = (
        mongo.db.notification_deliveries.update_many(
            {
                "status": "processing",
                "lease_expires_at": {
                    "$lte": timestamp,
                },
            },
            {
                "$set": {
                    "status": "pending",
                    "next_attempt_at": (
                        timestamp
                    ),
                    "lease_expires_at": None,
                    "last_error_code": (
                        WORKER_LEASE_EXPIRED_ERROR_CODE
                    ),
                    "updated_at": timestamp,
                },
            },
        )
    )

    return result.modified_count


def mark_notification_delivery_delivered(
    delivery_id,
    *,
    expected_attempt_count,
    now=None,
):
    generation = _normalize_expected_attempt_count(expected_attempt_count)
    timestamp = _normalize_now(
        now
    )

    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    updated = (
        mongo.db.notification_deliveries.find_one_and_update(
            {
                "_id": delivery_object_id,
                "status": "processing",
                "attempt_count": generation,
            },
            {
                "$set": {
                    "status": "delivered",
                    "next_attempt_at": None,
                    "lease_expires_at": None,
                    "delivered_at": timestamp,
                    "failed_at": None,
                    "suppressed_at": None,
                    "last_error_code": None,
                    "updated_at": timestamp,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    updated = (
        _normalize_mongo_delivery_document(
            updated
        )
    )

    if updated is None:
        return None

    return updated


def reschedule_notification_delivery(
    delivery_id,
    *,
    expected_attempt_count,
    next_attempt_at,
    error_code,
    now=None,
):
    generation = _normalize_expected_attempt_count(expected_attempt_count)
    timestamp = _normalize_now(
        now
    )

    retry_at = _normalize_timestamp(
        next_attempt_at,
        (
            "notification delivery "
            "next_attempt_at"
        ),
    )

    if retry_at < timestamp:
        raise ValueError(
            "Notification delivery retry time "
            "cannot precede the current time."
        )

    normalized_error_code = (
        normalize_notification_delivery_error_code(
            error_code
        )
    )

    if normalized_error_code is None:
        raise ValueError(
            "Notification delivery retry requires "
            "an error code."
        )

    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    updated = (
        mongo.db.notification_deliveries.find_one_and_update(
            {
                "_id": delivery_object_id,
                "status": "processing",
                "attempt_count": generation,
            },
            {
                "$set": {
                    "status": "pending",
                    "next_attempt_at": retry_at,
                    "lease_expires_at": None,
                    "delivered_at": None,
                    "failed_at": None,
                    "suppressed_at": None,
                    "last_error_code": (
                        normalized_error_code
                    ),
                    "updated_at": timestamp,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    updated = (
        _normalize_mongo_delivery_document(
            updated
        )
    )

    if updated is None:
        return None

    return updated


def mark_notification_delivery_failed(
    delivery_id,
    *,
    expected_attempt_count,
    error_code,
    now=None,
):
    generation = _normalize_expected_attempt_count(expected_attempt_count)
    timestamp = _normalize_now(
        now
    )

    normalized_error_code = (
        normalize_notification_delivery_error_code(
            error_code
        )
    )

    if normalized_error_code is None:
        raise ValueError(
            "Failed Notification delivery "
            "requires an error code."
        )

    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    updated = (
        mongo.db.notification_deliveries.find_one_and_update(
            {
                "_id": delivery_object_id,
                "status": "processing",
                "attempt_count": generation,
            },
            {
                "$set": {
                    "status": "failed",
                    "next_attempt_at": None,
                    "lease_expires_at": None,
                    "delivered_at": None,
                    "failed_at": timestamp,
                    "suppressed_at": None,
                    "last_error_code": (
                        normalized_error_code
                    ),
                    "updated_at": timestamp,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    updated = (
        _normalize_mongo_delivery_document(
            updated
        )
    )

    if updated is None:
        return None

    return updated


def suppress_pending_notification_delivery(
    delivery_id,
    *,
    now=None,
    error_code=None,
):
    timestamp = _normalize_now(
        now
    )

    normalized_error_code = (
        normalize_notification_delivery_error_code(
            error_code
        )
    )

    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    updated = (
        mongo.db.notification_deliveries.find_one_and_update(
            {
                "_id": delivery_object_id,
                "status": "pending",
            },
            {
                "$set": {
                    "status": "suppressed",
                    "next_attempt_at": None,
                    "lease_expires_at": None,
                    "delivered_at": None,
                    "failed_at": None,
                    "suppressed_at": (
                        timestamp
                    ),
                    "last_error_code": (
                        normalized_error_code
                    ),
                    "updated_at": timestamp,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    updated = (
        _normalize_mongo_delivery_document(
            updated
        )
    )

    if updated is None:
        return None

    return updated


def mark_notification_delivery_suppressed(
    delivery_id,
    *,
    expected_attempt_count,
    now=None,
    error_code=None,
):
    generation = _normalize_expected_attempt_count(expected_attempt_count)
    timestamp = _normalize_now(
        now
    )

    normalized_error_code = (
        normalize_notification_delivery_error_code(
            error_code
        )
    )

    delivery_object_id = _to_object_id(
        delivery_id,
        field_name=(
            "notification delivery ID"
        ),
    )

    updated = (
        mongo.db.notification_deliveries.find_one_and_update(
            {
                "_id": delivery_object_id,
                "status": "processing",
                "attempt_count": generation,
            },
            {
                "$set": {
                    "status": "suppressed",
                    "next_attempt_at": None,
                    "lease_expires_at": None,
                    "delivered_at": None,
                    "failed_at": None,
                    "suppressed_at": (
                        timestamp
                    ),
                    "last_error_code": (
                        normalized_error_code
                    ),
                    "updated_at": timestamp,
                },
            },
            return_document=(
                ReturnDocument.AFTER
            ),
        )
    )

    updated = (
        _normalize_mongo_delivery_document(
            updated
        )
    )

    if updated is None:
        return None

    return updated


__all__ = [
    "DEFAULT_NOTIFICATION_DELIVERY_LEASE_SECONDS",
    "MAX_NOTIFICATION_DELIVERY_LEASE_SECONDS",
    "WORKER_LEASE_EXPIRED_ERROR_CODE",
    "claim_next_due_notification_delivery",
    "create_notification_delivery_idempotent",
    "get_notification_delivery_by_id",
    "get_notification_delivery_by_notification_channel",
    "mark_notification_delivery_delivered",
    "mark_notification_delivery_failed",
    "mark_notification_delivery_suppressed",
    "suppress_pending_notification_delivery",
    "recover_expired_notification_delivery_leases",
    "reschedule_notification_delivery",
    "utc_now",
]
