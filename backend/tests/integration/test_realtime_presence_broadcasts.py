"""End-to-end Socket.IO tests for Soultie private presence broadcasts."""

import time
from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.extensions import (
    mongo,
    redis_store,
    socketio,
)
from app.services.auth_service import (
    issue_session,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_presence_broadcasts"
)

TEST_PREFIX = (
    "xuoroni:test:realtime-presence-broadcasts"
)

TEST_TTL_SECONDS = 2

TEST_HEARTBEAT_SECONDS = 1


@pytest.fixture()
def app():
    application = create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": "",
            "SECRET_KEY": (
                "xuoroni-presence-broadcast-"
                "flask-secret"
            ),
            "MONGO_URI": (
                "mongodb://localhost:27017/"
                + TEST_DATABASE
            ),
            "JWT_SECRET_KEY": (
                "xuoroni-presence-broadcast-"
                "jwt-secret-at-least-32-bytes"
            ),
            "JWT_ISSUER": "xuoroni-test",
            "JWT_AUDIENCE": (
                "xuoroni-test-client"
            ),
            "JWT_ALGORITHM": "HS256",
            "JWT_ACCESS_TOKEN_MINUTES": 15,
            "JWT_REFRESH_TOKEN_DAYS": 30,
            "REALTIME_PRESENCE_KEY_PREFIX": (
                TEST_PREFIX
            ),
            "REALTIME_PRESENCE_TTL_SECONDS": (
                TEST_TTL_SECONDS
            ),
            "REALTIME_PRESENCE_HEARTBEAT_SECONDS": (
                TEST_HEARTBEAT_SECONDS
            ),
        }
    )

    with application.app_context():
        mongo.cx.drop_database(
            TEST_DATABASE
        )

        redis = (
            redis_store.get_client()
        )

        keys = list(
            redis.scan_iter(
                match=f"{TEST_PREFIX}:*"
            )
        )

        if keys:
            redis.delete(
                *keys
            )

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )

        keys = list(
            redis.scan_iter(
                match=f"{TEST_PREFIX}:*"
            )
        )

        if keys:
            redis.delete(
                *keys
            )


def _create_user():
    user_id = ObjectId()

    now = datetime.now(
        timezone.utc
    )

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )

    mongo.db.profiles.insert_one(
        {
            "_id": ObjectId(),
            "user_id": user_id,
            "onboarding_status": "completed",
            "profile_status": "active",
            "visibility": "visible",
            "created_at": now,
            "updated_at": now,
        }
    )

    tokens = issue_session(
        user_id,
        metadata={
            "source": (
                "presence-broadcast-test"
            ),
        },
    )

    return (
        user_id,
        tokens,
    )


def _create_match(
    first_user_id,
    second_user_id,
    *,
    status="active",
):
    now = datetime.now(
        timezone.utc
    )

    match_id = ObjectId()

    mongo.db.matches.insert_one(
        {
            "_id": match_id,
            "user_ids": [
                first_user_id,
                second_user_id,
            ],
            "status": status,
            "matched_at": now,
            "updated_at": now,
        }
    )

    return match_id


def _connect(
    app,
    tokens,
):
    return socketio.test_client(
        app,
        namespace="/",
        auth={
            "access_token": (
                tokens[
                    "access_token"
                ]
            ),
        },
    )


def _received_named(
    client,
    event_name,
):
    return [
        event
        for event
        in client.get_received(
            namespace="/"
        )
        if event.get(
            "name"
        )
        == event_name
    ]


def _clear_received(
    *clients,
):
    for client in clients:
        if client.is_connected(
            "/"
        ):
            client.get_received(
                namespace="/"
            )


def _disconnect(
    *clients,
):
    for client in clients:
        if (
            client is not None
            and client.is_connected(
                "/"
            )
        ):
            client.disconnect(
                namespace="/"
            )


def test_matched_observer_receives_online_and_final_offline(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject = None

    try:
        assert observer.is_connected(
            "/"
        )

        _clear_received(
            observer
        )

        subject = _connect(
            app,
            subject_tokens,
        )

        assert subject.is_connected(
            "/"
        )

        online = _received_named(
            observer,
            "presence.online",
        )

        assert len(
            online
        ) == 1

        assert (
            online[
                0
            ][
                "args"
            ][
                0
            ]
            == {
                "user_id": str(
                    subject_id
                ),
                "online": True,
            }
        )

        subject.disconnect(
            namespace="/"
        )

        offline = _received_named(
            observer,
            "presence.offline",
        )

        assert len(
            offline
        ) == 1

        assert (
            offline[
                0
            ][
                "args"
            ][
                0
            ]
            == {
                "user_id": str(
                    subject_id
                ),
                "online": False,
            }
        )

    finally:
        _disconnect(
            subject,
            observer,
        )


def test_second_subject_socket_does_not_duplicate_online(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject_one = None
    subject_two = None

    try:
        _clear_received(
            observer
        )

        subject_one = _connect(
            app,
            subject_tokens,
        )

        first_online = _received_named(
            observer,
            "presence.online",
        )

        assert len(
            first_online
        ) == 1

        assert (
            first_online[
                0
            ][
                "args"
            ][
                0
            ][
                "user_id"
            ]
            == str(
                subject_id
            )
        )

        subject_two = _connect(
            app,
            subject_tokens,
        )

        duplicate_online = (
            _received_named(
                observer,
                "presence.online",
            )
        )

        assert (
            duplicate_online
            == []
        )

    finally:
        _disconnect(
            subject_one,
            subject_two,
            observer,
        )


def test_first_of_two_subject_disconnects_does_not_emit_offline(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject_one = _connect(
        app,
        subject_tokens,
    )

    subject_two = _connect(
        app,
        subject_tokens,
    )

    try:
        _clear_received(
            observer
        )

        subject_one.disconnect(
            namespace="/"
        )

        premature_offline = (
            _received_named(
                observer,
                "presence.offline",
            )
        )

        assert (
            premature_offline
            == []
        )

        subject_two.disconnect(
            namespace="/"
        )

        final_offline = (
            _received_named(
                observer,
                "presence.offline",
            )
        )

        assert len(
            final_offline
        ) == 1

        assert (
            final_offline[
                0
            ][
                "args"
            ][
                0
            ]
            == {
                "user_id": str(
                    subject_id
                ),
                "online": False,
            }
        )

    finally:
        _disconnect(
            subject_one,
            subject_two,
            observer,
        )


def test_all_observer_devices_receive_presence_transition(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

    observer_one = _connect(
        app,
        observer_tokens,
    )

    observer_two = _connect(
        app,
        observer_tokens,
    )

    subject = None

    try:
        _clear_received(
            observer_one,
            observer_two,
        )

        subject = _connect(
            app,
            subject_tokens,
        )

        events_one = (
            _received_named(
                observer_one,
                "presence.online",
            )
        )

        events_two = (
            _received_named(
                observer_two,
                "presence.online",
            )
        )

        assert len(
            events_one
        ) == 1

        assert len(
            events_two
        ) == 1

        assert (
            events_one[
                0
            ][
                "args"
            ][
                0
            ][
                "user_id"
            ]
            == str(
                subject_id
            )
        )

        assert (
            events_two[
                0
            ][
                "args"
            ][
                0
            ][
                "user_id"
            ]
            == str(
                subject_id
            )
        )

    finally:
        _disconnect(
            subject,
            observer_one,
            observer_two,
        )


def test_blocked_match_receives_no_presence_transition(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

        mongo.db.blocks.insert_one(
            {
                "_id": ObjectId(),
                "blocker_id": (
                    observer_id
                ),
                "blocked_id": (
                    subject_id
                ),
                "created_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            }
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject = None

    try:
        _clear_received(
            observer
        )

        subject = _connect(
            app,
            subject_tokens,
        )

        assert (
            _received_named(
                observer,
                "presence.online",
            )
            == []
        )

        subject.disconnect(
            namespace="/"
        )

        assert (
            _received_named(
                observer,
                "presence.offline",
            )
            == []
        )

    finally:
        _disconnect(
            subject,
            observer,
        )


def test_unmatched_relationship_receives_no_presence_transition(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
            status="unmatched",
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject = None

    try:
        _clear_received(
            observer
        )

        subject = _connect(
            app,
            subject_tokens,
        )

        assert (
            _received_named(
                observer,
                "presence.online",
            )
            == []
        )

        subject.disconnect(
            namespace="/"
        )

        assert (
            _received_named(
                observer,
                "presence.offline",
            )
            == []
        )

    finally:
        _disconnect(
            subject,
            observer,
        )


def test_heartbeat_restores_online_transition_after_presence_ttl_expiry(
    app,
):
    with app.app_context():
        observer_id, observer_tokens = (
            _create_user()
        )

        subject_id, subject_tokens = (
            _create_user()
        )

        _create_match(
            observer_id,
            subject_id,
        )

    observer = _connect(
        app,
        observer_tokens,
    )

    subject = _connect(
        app,
        subject_tokens,
    )

    try:
        initial_online = (
            _received_named(
                observer,
                "presence.online",
            )
        )

        assert len(
            initial_online
        ) == 1

        time.sleep(
            TEST_TTL_SECONDS
            + 0.35
        )

        with app.app_context():
            redis = (
                redis_store.get_client()
            )

            subject_keys = list(
                redis.scan_iter(
                    match=(
                        f"{TEST_PREFIX}:"
                        f"{subject_id}:*"
                    )
                )
            )

            assert (
                subject_keys
                == []
            )

        response = subject.emit(
            "presence.heartbeat",
            {
                # Must never control emitted identity.
                "user_id": str(
                    ObjectId()
                ),
            },
            namespace="/",
            callback=True,
        )

        assert response[
            "ok"
        ] is True

        assert response[
            "changed"
        ] is True

        assert (
            response[
                "user_id"
            ]
            == str(
                subject_id
            )
        )

        restored_online = (
            _received_named(
                observer,
                "presence.online",
            )
        )

        assert len(
            restored_online
        ) == 1

        assert (
            restored_online[
                0
            ][
                "args"
            ][
                0
            ]
            == {
                "user_id": str(
                    subject_id
                ),
                "online": True,
            }
        )

    finally:
        _disconnect(
            subject,
            observer,
        )