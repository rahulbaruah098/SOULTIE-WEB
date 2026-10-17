"""MongoDB cafe booking transaction boundary contracts.

These are mocked unit/integration-boundary tests, not real MongoDB
concurrency or transaction durability tests.
"""

from types import SimpleNamespace
from unittest.mock import Mock

from pymongo.errors import AutoReconnect, OperationFailure, PyMongoError
import pytest

from app.services import cafe_reservation_transaction_service as service


class FakeSession:
    def __init__(self, *, retries=1, active=True, error=None):
        self.in_transaction = False
        self.retries = retries
        self.active = active
        self.error = error
        self.entered = False
        self.exited = False
        self.attempts = 0

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False

    def with_transaction(self, callback):
        if self.error is not None:
            raise self.error
        result = None
        for _ in range(self.retries):
            self.attempts += 1
            self.in_transaction = self.active
            try:
                result = callback(self)
            finally:
                self.in_transaction = False
        return result


@pytest.fixture
def context(monkeypatch):
    session = FakeSession()
    client = Mock()
    client.start_session.return_value = session
    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(cx=client),
    )
    return SimpleNamespace(client=client, session=session)


def run(operation):
    return service.run_cafe_reservation_transaction(
        operation=operation
    )


def test_success_returns_callback_result(context):
    value = {"reservation": "mock-result"}
    assert run(lambda session: value) is value
    assert context.session.entered
    assert context.session.exited
    assert context.session.attempts == 1
    context.client.start_session.assert_called_once_with()


def test_callback_receives_same_session(context):
    seen = []

    def operation(session):
        seen.append(session)
        assert session is context.session
        assert session.in_transaction is True
        return 12

    assert run(operation) == 12
    assert seen == [context.session]


def test_empty_callback_result_is_not_fabricated(context):
    assert run(lambda session: None) is None


@pytest.mark.parametrize(
    "callback",
    [None, 123, "not callable", {}, [], True],
)
def test_invalid_callback_rejected_before_connection(
    context, callback
):
    with pytest.raises(service.CafeReservationTransactionError):
        run(callback)
    context.client.start_session.assert_not_called()


def test_unavailable_client_fails_closed(context, monkeypatch):
    monkeypatch.setattr(
        service, "mongo", SimpleNamespace(cx=None)
    )
    with pytest.raises(service.CafeReservationTransactionError):
        run(lambda session: "unsafe")


def test_missing_session_fails_closed(context):
    context.client.start_session.return_value = None
    with pytest.raises(
        (service.CafeReservationTransactionError, TypeError)
    ):
        run(lambda session: "unsafe")


def test_missing_transaction_method_fails_closed(context):
    class BadSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    context.client.start_session.return_value = BadSession()
    with pytest.raises(service.CafeReservationTransactionError):
        run(lambda session: "unsafe")


@pytest.mark.parametrize(
    "active",
    [False, None, 0, 1, "True"],
)
def test_inactive_or_invalid_transaction_rejected(
    context, active
):
    context.session.active = active
    executed = []

    def operation(session):
        executed.append(True)
        return "unsafe"

    with pytest.raises(service.CafeReservationTransactionError):
        run(operation)

    assert executed == []
    assert context.session.exited


@pytest.mark.parametrize(
    "error",
    [
        ValueError("callback failed"),
        RuntimeError("service failed"),
        OperationFailure("write conflict"),
        AutoReconnect("connection interrupted"),
    ],
)
def test_callback_failure_propagates_and_closes_session(
    context, error
):
    def operation(session):
        raise error

    with pytest.raises(type(error)):
        run(operation)
    assert context.session.exited


@pytest.mark.parametrize(
    "error",
    [
        PyMongoError("database failure"),
        OperationFailure("transaction failure"),
        AutoReconnect("reconnect"),
    ],
)
def test_transaction_driver_failure_propagates(
    context, error
):
    context.session.error = error
    with pytest.raises(type(error)):
        run(lambda session: "unsafe")
    assert context.session.exited


def test_start_session_failure_propagates(context):
    context.client.start_session.side_effect = AutoReconnect(
        "database unavailable"
    )
    with pytest.raises(AutoReconnect):
        run(lambda session: "unsafe")


def test_complete_callback_reexecuted_when_driver_retries(context):
    context.session.retries = 3
    calls = []

    def operation(session):
        assert session.in_transaction
        calls.append({
            "attempt": len(calls) + 1,
            "session": session,
        })
        return len(calls)

    assert run(operation) == 3
    assert len(calls) == 3
    assert all(
        attempt["session"] is context.session
        for attempt in calls
    )
    assert context.session.exited


def test_coordinator_does_not_retry_callback_itself(context):
    context.session.retries = 1
    attempts = []

    def operation(session):
        attempts.append(1)
        raise OperationFailure("write conflict")

    with pytest.raises(OperationFailure):
        run(operation)
    assert len(attempts) == 1


def test_callback_must_not_run_after_transaction_end(context):
    captured = []

    def operation(session):
        captured.append(session.in_transaction)
        return "result"

    assert run(operation) == "result"
    assert captured == [True]
    assert context.session.in_transaction is False


def test_transaction_service_does_not_create_reservation(context):
    result = run(lambda session: {"status": "test-only"})
    assert result == {"status": "test-only"}
    assert not hasattr(
        service, "create_reservation"
    )
    assert not hasattr(
        service, "confirm_reservation"
    )


def test_no_live_database_required(context):
    assert run(lambda session: "mock-only") == "mock-only"
    context.client.start_session.assert_called_once()
