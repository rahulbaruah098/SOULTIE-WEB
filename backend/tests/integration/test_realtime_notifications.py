"""Integration tests for privacy-scoped Xuoroni realtime notifications."""

from datetime import (
    datetime,
    timezone,
)

from bson import ObjectId
import pytest

from app import create_app
from app.extensions import (
    mongo,
    socketio,
)
import app.realtime.notifications as notifications
from app.realtime.notifications import (
    PRESENCE_OFFLINE_EVENT,
    PRESENCE_ONLINE_EVENT,
    RealtimeNotificationRoutingError,
    emit_presence_transition,
    join_private_user_room,
    list_presence_observer_ids,
    private_user_room_name,
)


TEST_DATABASE = (
    "xuoroni_test_realtime_notifications"
)


@pytest.fixture()
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

        yield application

        mongo.cx.drop_database(
            TEST_DATABASE
        )


def _insert_user(
    user_id,
    *,
    account_status="active",
):
    now = datetime.now(
        timezone.utc
    )

    mongo.db.users.insert_one(
        {
            "_id": user_id,
            "account_status": (
                account_status
            ),
            "created_at": now,
            "updated_at": now,
        }
    )


def _insert_profile(
    user_id,
    *,
    onboarding_status="completed",
    profile_status="active",
    visibility="visible",
):
    now = datetime.now(
        timezone.utc
    )

    mongo.db.profiles.insert_one(
        {
            "_id": ObjectId(),
            "user_id": user_id,
            "onboarding_status": (
                onboarding_status
            ),
            "profile_status": (
                profile_status
            ),
            "visibility": visibility,
            "created_at": now,
            "updated_at": now,
        }
    )


def _insert_match(
    first_user_id,
    second_user_id,
    *,
    status="active",
):
    mongo.db.matches.insert_one(
        {
            "_id": ObjectId(),
            "user_ids": [
                first_user_id,
                second_user_id,
            ],
            "status": status,
            "matched_at": (
                datetime.now(
                    timezone.utc
                )
            ),
            "updated_at": (
                datetime.now(
                    timezone.utc
                )
            ),
        }
    )


def test_private_user_room_name_contract(
    app,
):
    with app.app_context():
        assert (
            private_user_room_name(
                "507f191e810c19729de860ea"
            )
            == (
                "user:"
                "507f191e810c19729de860ea"
            )
        )

        for invalid_value in (
            None,
            "",
            "   ",
        ):
            with pytest.raises(
                ValueError
            ):
                private_user_room_name(
                    invalid_value
                )


def test_join_private_user_room_uses_server_controlled_room(
    app,
    monkeypatch,
):
    joined = []

    def fake_join_room(
        room,
    ):
        joined.append(
            room
        )

    monkeypatch.setattr(
        notifications,
        "join_room",
        fake_join_room,
    )

    with app.app_context():
        room = (
            join_private_user_room(
                "507f191e810c19729de860ea"
            )
        )

    assert (
        room
        == (
            "user:"
            "507f191e810c19729de860ea"
        )
    )

    assert joined == [
        (
            "user:"
            "507f191e810c19729de860ea"
        )
    ]


def test_observer_authorization_filters_relationship_safety_and_account_state(
    app,
):
    with app.app_context():
        subject = ObjectId()
        allowed = ObjectId()
        unmatched = ObjectId()
        blocked = ObjectId()
        inactive_account = ObjectId()
        incomplete_profile = ObjectId()
        inactive_profile = ObjectId()

        users = [
            subject,
            allowed,
            unmatched,
            blocked,
            inactive_account,
            incomplete_profile,
            inactive_profile,
        ]

        for user_id in users:
            _insert_user(
                user_id,
                account_status=(
                    "disabled"
                    if user_id
                    == inactive_account
                    else "active"
                ),
            )

        for user_id in users:
            _insert_profile(
                user_id,
                onboarding_status=(
                    "pending"
                    if user_id
                    == incomplete_profile
                    else "completed"
                ),
                profile_status=(
                    "paused"
                    if user_id
                    == inactive_profile
                    else "active"
                ),
            )

        _insert_match(
            subject,
            allowed,
        )

        _insert_match(
            subject,
            unmatched,
            status="unmatched",
        )

        _insert_match(
            subject,
            blocked,
        )

        _insert_match(
            subject,
            inactive_account,
        )

        _insert_match(
            subject,
            incomplete_profile,
        )

        _insert_match(
            subject,
            inactive_profile,
        )

        mongo.db.blocks.insert_one(
            {
                "_id": ObjectId(),
                "blocker_id": blocked,
                "blocked_id": subject,
                "created_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            }
        )

        observers = (
            list_presence_observer_ids(
                subject
            )
        )

        assert observers == [
            str(
                allowed
            )
        ]


def test_hidden_profile_remains_eligible_observer(
    app,
):
    with app.app_context():
        subject = ObjectId()
        peer = ObjectId()

        _insert_user(
            subject
        )

        _insert_user(
            peer
        )

        _insert_profile(
            subject
        )

        _insert_profile(
            peer,
            visibility="hidden",
        )

        _insert_match(
            subject,
            peer,
        )

        observers = (
            list_presence_observer_ids(
                subject
            )
        )

        assert observers == [
            str(
                peer
            )
        ]


def test_reverse_direction_block_is_symmetric(
    app,
):
    with app.app_context():
        subject = ObjectId()
        peer = ObjectId()

        _insert_user(
            subject
        )

        _insert_user(
            peer
        )

        _insert_profile(
            subject
        )

        _insert_profile(
            peer
        )

        _insert_match(
            subject,
            peer,
        )

        mongo.db.blocks.insert_one(
            {
                "_id": ObjectId(),
                "blocker_id": subject,
                "blocked_id": peer,
                "created_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            }
        )

        assert (
            list_presence_observer_ids(
                subject
            )
            == []
        )


def test_observer_lookup_failure_fails_closed(
    app,
    monkeypatch,
):
    def broken_peer_lookup(
        _user_id,
    ):
        raise RuntimeError(
            "database unavailable"
        )

    monkeypatch.setattr(
        notifications,
        "list_active_match_peer_ids_for_user",
        broken_peer_lookup,
    )

    with app.app_context():
        with pytest.raises(
            RealtimeNotificationRoutingError
        ) as exc_info:
            list_presence_observer_ids(
                ObjectId()
            )

    assert (
        exc_info.value.code
        == (
            "REALTIME_NOTIFICATION_"
            "ROUTING_UNAVAILABLE"
        )
    )


def test_emit_presence_online_targets_only_server_resolved_private_rooms(
    app,
    monkeypatch,
):
    subject = (
        "507f191e810c19729de860ea"
    )

    observer_one = (
        "507f191e810c19729de860eb"
    )

    observer_two = (
        "507f191e810c19729de860ec"
    )

    emitted = []

    def fake_observers(
        user_id,
    ):
        assert user_id == subject

        return [
            observer_one,
            observer_two,
        ]

    def fake_emit(
        event,
        payload,
        *,
        to,
        namespace,
    ):
        emitted.append(
            {
                "event": event,
                "payload": payload,
                "to": to,
                "namespace": namespace,
            }
        )

    monkeypatch.setattr(
        notifications,
        "list_presence_observer_ids",
        fake_observers,
    )

    monkeypatch.setattr(
        notifications.socketio,
        "emit",
        fake_emit,
    )

    with app.app_context():
        result = (
            emit_presence_transition(
                user_id=subject,
                online=True,
            )
        )

    assert result == {
        "event": (
            PRESENCE_ONLINE_EVENT
        ),
        "recipient_count": 2,
        "payload": {
            "user_id": subject,
            "online": True,
        },
    }

    assert emitted == [
        {
            "event": (
                PRESENCE_ONLINE_EVENT
            ),
            "payload": {
                "user_id": subject,
                "online": True,
            },
            "to": (
                "user:"
                + observer_one
            ),
            "namespace": "/",
        },
        {
            "event": (
                PRESENCE_ONLINE_EVENT
            ),
            "payload": {
                "user_id": subject,
                "online": True,
            },
            "to": (
                "user:"
                + observer_two
            ),
            "namespace": "/",
        },
    ]


def test_emit_presence_offline_uses_offline_event(
    app,
    monkeypatch,
):
    subject = (
        "507f191e810c19729de860ea"
    )

    observer = (
        "507f191e810c19729de860eb"
    )

    emitted = []

    monkeypatch.setattr(
        notifications,
        "list_presence_observer_ids",
        lambda _user_id: [
            observer
        ],
    )

    monkeypatch.setattr(
        notifications.socketio,
        "emit",
        lambda event, payload, *, to, namespace: emitted.append(
            {
                "event": event,
                "payload": payload,
                "to": to,
                "namespace": namespace,
            }
        ),
    )

    with app.app_context():
        result = (
            emit_presence_transition(
                user_id=subject,
                online=False,
            )
        )

    assert (
        result[
            "event"
        ]
        == PRESENCE_OFFLINE_EVENT
    )

    assert result[
        "recipient_count"
    ] == 1

    assert emitted == [
        {
            "event": (
                PRESENCE_OFFLINE_EVENT
            ),
            "payload": {
                "user_id": subject,
                "online": False,
            },
            "to": (
                "user:"
                + observer
            ),
            "namespace": "/",
        }
    ]


def test_emit_presence_with_no_observers_emits_nothing(
    app,
    monkeypatch,
):
    emitted = []

    monkeypatch.setattr(
        notifications,
        "list_presence_observer_ids",
        lambda _user_id: [],
    )

    monkeypatch.setattr(
        notifications.socketio,
        "emit",
        lambda *args, **kwargs: (
            emitted.append(
                (
                    args,
                    kwargs,
                )
            )
        ),
    )

    with app.app_context():
        result = (
            emit_presence_transition(
                user_id=(
                    "507f191e810c19729de860ea"
                ),
                online=True,
            )
        )

    assert result[
        "recipient_count"
    ] == 0

    assert emitted == []


def test_emit_presence_propagates_routing_failure_without_emitting(
    app,
    monkeypatch,
):
    emitted = []

    def fail_closed(
        _user_id,
    ):
        raise (
            RealtimeNotificationRoutingError()
        )

    monkeypatch.setattr(
        notifications,
        "list_presence_observer_ids",
        fail_closed,
    )

    monkeypatch.setattr(
        notifications.socketio,
        "emit",
        lambda *args, **kwargs: (
            emitted.append(
                (
                    args,
                    kwargs,
                )
            )
        ),
    )

    with app.app_context():
        with pytest.raises(
            RealtimeNotificationRoutingError
        ):
            emit_presence_transition(
                user_id=(
                    "507f191e810c19729de860ea"
                ),
                online=True,
            )

    assert emitted == []


def test_presence_events_are_outbound_only(
    app,
):
    handlers = (
        socketio.server.handlers.get(
            "/",
            {},
        )
    )

    assert (
        PRESENCE_ONLINE_EVENT
        not in handlers
    )

    assert (
        PRESENCE_OFFLINE_EVENT
        not in handlers
    )

    assert (
        "presence.subscribe"
        not in handlers
    )

    assert (
        "presence.join"
        not in handlers
    )

    assert (
        "user:join"
        not in handlers
    )

    assert (
        "presence.heartbeat"
        in handlers
    )