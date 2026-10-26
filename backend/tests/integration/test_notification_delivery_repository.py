"""Real-Mongo tests for durable Soultie Notification delivery state."""

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.db.indexes import ensure_indexes
from app.extensions import mongo
import app.repositories.notification_delivery_repository as delivery_repository
from app.repositories.notification_delivery_repository import (
    MAX_NOTIFICATION_DELIVERY_LEASE_SECONDS,
    claim_next_due_notification_delivery,
    create_notification_delivery_idempotent,
    get_notification_delivery_by_id,
    get_notification_delivery_by_notification_channel,
    mark_notification_delivery_delivered,
    mark_notification_delivery_failed,
    mark_notification_delivery_suppressed,
    suppress_pending_notification_delivery,
    recover_expired_notification_delivery_leases,
    reschedule_notification_delivery,
)
from app.schemas.notification_delivery import (
    build_notification_delivery_document,
)


TEST_DATABASE = (
    "xuoroni_test_notification_delivery_repository"
)


BASE_TIME = datetime(
    2026,
    10,
    16,
    7,
    0,
    tzinfo=timezone.utc,
)


@pytest.fixture(scope="module")
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        ensure_indexes(
            mongo.db
        )

    yield application

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )


@pytest.fixture(autouse=True)
def clean_delivery_state(
    app,
):
    with app.app_context():
        mongo.db.notification_deliveries.delete_many(
            {}
        )

    yield

    with app.app_context():
        mongo.db.notification_deliveries.delete_many(
            {}
        )


def _create(
    *,
    notification_id=None,
    user_id=None,
    channel="realtime",
    now=BASE_TIME,
    next_attempt_at=None,
):
    return create_notification_delivery_idempotent(
        notification_id=(
            notification_id
            or ObjectId()
        ),
        user_id=(
            user_id
            or ObjectId()
        ),
        channel=channel,
        now=now,
        next_attempt_at=next_attempt_at,
    )


def test_delivery_creation_roundtrip_normalizes_pymongo_naive_utc(
    app,
):
    with app.app_context():
        result = _create()

        delivery = result[
            "delivery"
        ]

        raw = (
            mongo.db.notification_deliveries.find_one(
                {
                    "_id": delivery[
                        "_id"
                    ],
                }
            )
        )

        loaded = (
            get_notification_delivery_by_id(
                delivery[
                    "_id"
                ]
            )
        )

    assert result[
        "created"
    ] is True

    assert raw[
        "created_at"
    ].tzinfo is None

    assert delivery[
        "created_at"
    ].tzinfo is not None

    assert delivery[
        "updated_at"
    ].tzinfo is not None

    assert delivery[
        "next_attempt_at"
    ].tzinfo is not None

    assert loaded[
        "created_at"
    ].tzinfo is not None

    assert loaded[
        "created_at"
    ] == BASE_TIME


def test_delivery_creation_is_idempotent(
    app,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    with app.app_context():
        first = _create(
            notification_id=notification_id,
            user_id=user_id,
        )

        second = _create(
            notification_id=notification_id,
            user_id=user_id,
            now=(
                BASE_TIME
                + timedelta(
                    seconds=1
                )
            ),
        )

        count = (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": notification_id,
                    "channel": "realtime",
                }
            )
        )

    assert first[
        "created"
    ] is True

    assert second[
        "created"
    ] is False

    assert (
        first[
            "delivery"
        ][
            "_id"
        ]
        == second[
            "delivery"
        ][
            "_id"
        ]
    )

    assert count == 1


def test_duplicate_key_race_recovers_existing_delivery(
    app,
    monkeypatch,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    existing_document = (
        build_notification_delivery_document(
            notification_id=notification_id,
            user_id=user_id,
            channel="realtime",
            now=BASE_TIME,
        )
    )

    with app.app_context():
        inserted = (
            mongo.db.notification_deliveries.insert_one(
                existing_document
            )
        )

        real_find = (
            delivery_repository
            ._find_by_notification_channel
        )

        calls = {
            "count": 0,
        }

        def race_find(
            candidate_notification_id,
            candidate_channel,
        ):
            calls[
                "count"
            ] += 1

            if calls[
                "count"
            ] == 1:
                return None

            return real_find(
                candidate_notification_id,
                candidate_channel,
            )

        monkeypatch.setattr(
            delivery_repository,
            "_find_by_notification_channel",
            race_find,
        )

        result = (
            create_notification_delivery_idempotent(
                notification_id=notification_id,
                user_id=user_id,
                channel="realtime",
                now=BASE_TIME,
            )
        )

        count = (
            mongo.db.notification_deliveries.count_documents(
                {
                    "notification_id": notification_id,
                    "channel": "realtime",
                }
            )
        )

    assert calls[
        "count"
    ] == 2

    assert result[
        "created"
    ] is False

    assert (
        result[
            "delivery"
        ][
            "_id"
        ]
        == inserted.inserted_id
    )

    assert count == 1


def test_existing_delivery_owner_conflict_fails_closed(
    app,
):
    notification_id = ObjectId()
    owner_id = ObjectId()

    with app.app_context():
        _create(
            notification_id=notification_id,
            user_id=owner_id,
        )

        with pytest.raises(
            ValueError,
            match="owner conflicts",
        ):
            _create(
                notification_id=notification_id,
                user_id=ObjectId(),
            )


def test_one_notification_supports_independent_channels(
    app,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    with app.app_context():
        realtime = _create(
            notification_id=notification_id,
            user_id=user_id,
            channel="realtime",
        )

        email = _create(
            notification_id=notification_id,
            user_id=user_id,
            channel="email",
        )

        push = _create(
            notification_id=notification_id,
            user_id=user_id,
            channel="push",
        )

    assert realtime[
        "delivery"
    ][
        "_id"
    ] != email[
        "delivery"
    ][
        "_id"
    ]

    assert email[
        "delivery"
    ][
        "_id"
    ] != push[
        "delivery"
    ][
        "_id"
    ]


def test_delivery_lookup_boundaries_return_canonical_documents(
    app,
):
    notification_id = ObjectId()
    user_id = ObjectId()

    with app.app_context():
        result = _create(
            notification_id=notification_id,
            user_id=user_id,
            channel="email",
        )

        by_id = (
            get_notification_delivery_by_id(
                result[
                    "delivery"
                ][
                    "_id"
                ]
            )
        )

        by_identity = (
            get_notification_delivery_by_notification_channel(
                notification_id=notification_id,
                channel=" EMAIL ",
            )
        )

    assert (
        by_id[
            "_id"
        ]
        == result[
            "delivery"
        ][
            "_id"
        ]
    )

    assert (
        by_identity[
            "_id"
        ]
        == result[
            "delivery"
        ][
            "_id"
        ]
    )

    assert by_id[
        "created_at"
    ].tzinfo is not None

    assert by_identity[
        "created_at"
    ].tzinfo is not None


def test_claim_only_selects_due_delivery_and_is_single_owner(
    app,
):
    user_id = ObjectId()

    with app.app_context():
        future = _create(
            user_id=user_id,
            channel="realtime",
            next_attempt_at=(
                BASE_TIME
                + timedelta(
                    minutes=10
                )
            ),
        )

        due = _create(
            user_id=user_id,
            channel="realtime",
            now=BASE_TIME,
            next_attempt_at=BASE_TIME,
        )

        first_claim = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
                channel="realtime",
            )
        )

        second_claim = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
                channel="realtime",
            )
        )

    assert (
        first_claim[
            "_id"
        ]
        == due[
            "delivery"
        ][
            "_id"
        ]
    )

    assert second_claim is None

    assert (
        first_claim[
            "_id"
        ]
        != future[
            "delivery"
        ][
            "_id"
        ]
    )


def test_claim_sets_processing_lease_and_increments_attempt(
    app,
):
    with app.app_context():
        result = _create()

        claimed = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
                lease_seconds=60,
                channel="realtime",
            )
        )

    assert (
        claimed[
            "_id"
        ]
        == result[
            "delivery"
        ][
            "_id"
        ]
    )

    assert claimed[
        "status"
    ] == "processing"

    assert claimed[
        "attempt_count"
    ] == 1

    assert claimed[
        "next_attempt_at"
    ] is None

    assert claimed[
        "last_attempt_at"
    ] == BASE_TIME

    assert (
        claimed[
            "lease_expires_at"
        ]
        == (
            BASE_TIME
            + timedelta(
                seconds=60
            )
        )
    )

    assert claimed[
        "lease_expires_at"
    ].tzinfo is not None


def test_claim_rejects_invalid_lease_durations(
    app,
):
    with app.app_context():
        _create()

        for invalid in (
            0,
            -1,
            True,
            1.5,
            (
                MAX_NOTIFICATION_DELIVERY_LEASE_SECONDS
                + 1
            ),
        ):
            with pytest.raises(
                ValueError
            ):
                claim_next_due_notification_delivery(
                    now=BASE_TIME,
                    lease_seconds=invalid,
                )


def test_reschedule_blocks_early_claim_and_next_claim_increments_attempt(
    app,
):
    with app.app_context():
        _create()

        claimed = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
                lease_seconds=60,
            )
        )

        retry_at = (
            BASE_TIME
            + timedelta(
                minutes=5
            )
        )

        rescheduled = (
            reschedule_notification_delivery(
                claimed[
                    "_id"
                ],
                next_attempt_at=retry_at,
                error_code="TRANSPORT_UNAVAILABLE",
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=2
                    )
                ),
            expected_attempt_count=1,
)
        )

        early = (
            claim_next_due_notification_delivery(
                now=(
                    retry_at
                    - timedelta(
                        seconds=1
                    )
                )
            )
        )

        claimed_again = (
            claim_next_due_notification_delivery(
                now=retry_at,
                lease_seconds=60,
            )
        )

    assert rescheduled[
        "status"
    ] == "pending"

    assert rescheduled[
        "attempt_count"
    ] == 1

    assert rescheduled[
        "next_attempt_at"
    ] == retry_at

    assert rescheduled[
        "last_error_code"
    ] == "TRANSPORT_UNAVAILABLE"

    assert early is None

    assert claimed_again[
        "attempt_count"
    ] == 2


def test_expired_processing_lease_is_recovered_at_boundary(
    app,
):
    with app.app_context():
        result = _create(
            channel="push",
        )

        claimed = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
                lease_seconds=60,
                channel="push",
            )
        )

        before = (
            recover_expired_notification_delivery_leases(
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=59
                    )
                )
            )
        )

        at_expiry = (
            recover_expired_notification_delivery_leases(
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=60
                    )
                )
            )
        )

        recovered = (
            get_notification_delivery_by_id(
                claimed[
                    "_id"
                ]
            )
        )

        reclaimed = (
            claim_next_due_notification_delivery(
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=60
                    )
                ),
                channel="push",
            )
        )

    assert (
        claimed[
            "_id"
        ]
        == result[
            "delivery"
        ][
            "_id"
        ]
    )

    assert before == 0

    assert at_expiry == 1

    assert recovered[
        "status"
    ] == "pending"

    assert recovered[
        "lease_expires_at"
    ] is None

    assert (
        recovered[
            "last_error_code"
        ]
        == "WORKER_LEASE_EXPIRED"
    )

    assert reclaimed[
        "attempt_count"
    ] == 2


def test_delivered_transition_is_processing_only_and_terminal(
    app,
):
    with app.app_context():
        result = _create()

        assert (
            mark_notification_delivery_delivered(
                result[
                    "delivery"
                ][
                    "_id"
                ],
                now=BASE_TIME,
            expected_attempt_count=1,
)
            is None
        )

        claimed = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
            )
        )

        delivered = (
            mark_notification_delivery_delivered(
                claimed[
                    "_id"
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=1
                    )
                ),
            expected_attempt_count=1,
)
        )

        repeated = (
            mark_notification_delivery_delivered(
                claimed[
                    "_id"
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=2
                    )
                ),
            expected_attempt_count=1,
)
        )

        failed_after = (
            mark_notification_delivery_failed(
                claimed[
                    "_id"
                ],
                error_code="SHOULD_NOT_APPLY",
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=2
                    )
                ),
            expected_attempt_count=1,
)
        )

    assert delivered[
        "status"
    ] == "delivered"

    assert delivered[
        "delivered_at"
    ].tzinfo is not None

    assert repeated is None

    assert failed_after is None


def test_failed_transition_requires_processing_and_is_terminal(
    app,
):
    with app.app_context():
        result = _create()

        assert (
            mark_notification_delivery_failed(
                result[
                    "delivery"
                ][
                    "_id"
                ],
                error_code="SHOULD_NOT_APPLY",
                now=BASE_TIME,
            expected_attempt_count=1,
)
            is None
        )

        claimed = (
            claim_next_due_notification_delivery(
                now=BASE_TIME,
            )
        )

        failed = (
            mark_notification_delivery_failed(
                claimed[
                    "_id"
                ],
                error_code="MAX_ATTEMPTS_REACHED",
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=1
                    )
                ),
            expected_attempt_count=1,
)
        )

        delivered_after = (
            mark_notification_delivery_delivered(
                claimed[
                    "_id"
                ],
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=2
                    )
                ),
            expected_attempt_count=1,
)
        )

    assert failed[
        "status"
    ] == "failed"

    assert (
        failed[
            "last_error_code"
        ]
        == "MAX_ATTEMPTS_REACHED"
    )

    assert failed[
        "failed_at"
    ].tzinfo is not None

    assert failed[
        "next_attempt_at"
    ] is None

    assert delivered_after is None


def test_pending_delivery_can_be_suppressed_without_attempt(
    app,
):
    with app.app_context():
        result = _create(
            channel="email",
        )

        suppressed = (
            suppress_pending_notification_delivery(
                result[
                    "delivery"
                ][
                    "_id"
                ],
                error_code="CHANNEL_DISABLED",
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=1
                    )
                ),
            )
        )

        claimed_after = (
            claim_next_due_notification_delivery(
                now=(
                    BASE_TIME
                    + timedelta(
                        seconds=2
                    )
                ),
                channel="email",
            )
        )

    assert suppressed[
        "status"
    ] == "suppressed"

    assert suppressed[
        "attempt_count"
    ] == 0

    assert (
        suppressed[
            "last_error_code"
        ]
        == "CHANNEL_DISABLED"
    )

    assert suppressed[
        "suppressed_at"
    ].tzinfo is not None

    assert suppressed[
        "next_attempt_at"
    ] is None

    assert claimed_after is None

def test_stale_worker_generation_fences_processing_transitions(app):
    with app.app_context():
        created = _create(channel="push")
        delivery_id = created["delivery"]["_id"]

        first = claim_next_due_notification_delivery(
            now=BASE_TIME, lease_seconds=60, channel="push"
        )
        assert first["attempt_count"] == 1

        assert recover_expired_notification_delivery_leases(
            now=BASE_TIME + timedelta(seconds=60)
        ) == 1

        second = claim_next_due_notification_delivery(
            now=BASE_TIME + timedelta(seconds=60),
            lease_seconds=60,
            channel="push",
        )
        assert second["attempt_count"] == 2
        assert second["last_error_code"] is None

        later = BASE_TIME + timedelta(seconds=61)

        assert mark_notification_delivery_delivered(
            delivery_id, expected_attempt_count=1, now=later
        ) is None

        assert reschedule_notification_delivery(
            delivery_id,
            expected_attempt_count=1,
            next_attempt_at=later + timedelta(seconds=30),
            error_code="STALE",
            now=later,
        ) is None

        assert mark_notification_delivery_failed(
            delivery_id,
            expected_attempt_count=1,
            error_code="STALE",
            now=later,
        ) is None

        assert mark_notification_delivery_suppressed(
            delivery_id,
            expected_attempt_count=1,
            error_code="STALE",
            now=later,
        ) is None

        assert suppress_pending_notification_delivery(
            delivery_id, error_code="STALE", now=later
        ) is None

        current = get_notification_delivery_by_id(delivery_id)
        assert current["status"] == "processing"
        assert current["attempt_count"] == 2
        assert current["last_error_code"] is None

        completed = mark_notification_delivery_delivered(
            delivery_id, expected_attempt_count=2, now=later
        )
        assert completed["status"] == "delivered"


@pytest.mark.parametrize(
    "invalid", [0, -1, True, False, None, "1"]
)
def test_invalid_claim_generation_fails_closed(app, invalid):
    with app.app_context():
        calls = (
            lambda: mark_notification_delivery_delivered(
                ObjectId(), expected_attempt_count=invalid
            ),
            lambda: mark_notification_delivery_failed(
                ObjectId(),
                expected_attempt_count=invalid,
                error_code="TEST",
            ),
            lambda: reschedule_notification_delivery(
                ObjectId(),
                expected_attempt_count=invalid,
                next_attempt_at=BASE_TIME,
                error_code="TEST",
                now=BASE_TIME,
            ),
            lambda: mark_notification_delivery_suppressed(
                ObjectId(), expected_attempt_count=invalid
            ),
        )
        for transition in calls:
            with pytest.raises(ValueError):
                transition()
