"""Regression tests for shared Socket.IO app-factory isolation."""

from socketio.pubsub_manager import (
    PubSubManager,
)

from app import create_app
from app.extensions import socketio


QUEUE_URL = (
    "redis://localhost:6379/15"
)


def _create_test_app(
    message_queue,
):
    return create_app(
        {
            "TESTING": True,
            "AUTO_ENSURE_INDEXES": False,
            "SOCKETIO_MESSAGE_QUEUE": (
                message_queue
            ),
        }
    )


def test_disabled_queue_does_not_inherit_previous_pubsub_manager():
    queued_app = (
        _create_test_app(
            QUEUE_URL
        )
    )

    assert queued_app is not None

    assert isinstance(
        socketio.server.manager,
        PubSubManager,
    )

    unqueued_app = (
        _create_test_app(
            ""
        )
    )

    assert not isinstance(
        socketio.server.manager,
        PubSubManager,
    )

    # This is the exact operation that previously raised:
    #
    # RuntimeError:
    # Test client cannot be used with a message queue.
    client = socketio.test_client(
        unqueued_app,
        namespace="/",
        auth={},
    )

    # Missing auth is intentionally rejected by Soultie,
    # but constructing the Socket.IO test client itself
    # must be possible when the queue is disabled.
    assert (
        client.is_connected(
            "/"
        )
        is False
    )


def test_queue_can_be_enabled_and_disabled_repeatedly():
    first_unqueued = (
        _create_test_app(
            ""
        )
    )

    assert first_unqueued is not None

    assert not isinstance(
        socketio.server.manager,
        PubSubManager,
    )

    queued = (
        _create_test_app(
            QUEUE_URL
        )
    )

    assert queued is not None

    assert isinstance(
        socketio.server.manager,
        PubSubManager,
    )

    final_unqueued = (
        _create_test_app(
            ""
        )
    )

    assert final_unqueued is not None

    assert not isinstance(
        socketio.server.manager,
        PubSubManager,
    )

    client = socketio.test_client(
        final_unqueued,
        namespace="/",
        auth={},
    )

    assert (
        client.is_connected(
            "/"
        )
        is False
    )