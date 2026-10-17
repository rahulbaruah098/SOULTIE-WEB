"""MongoDB transaction boundary for Xuoroni cafe reservations.

Booking writes must be performed exclusively inside this coordinator,
using the provided session for EVERY database read and write.

MongoDB/PyMongo with_transaction may rerun the callback on transient
transaction errors. The entire operation must be repeatable and MUST
not send email, push notifications or other external side effects
inside the transaction callback.

A reservation must be persisted together with an outbox event in a
later implementation, then dispatched only after commit.

No reservation is created by this module.
"""

from collections.abc import Callable

from app.extensions import mongo


class CafeReservationTransactionError(RuntimeError):
    """The reservation transaction contract was not satisfied."""


def run_cafe_reservation_transaction(*, operation):
    """Run a booking operation inside a MongoDB transaction.

    The operation callback receives only the active transaction
    session. It must acquire the venue write fence before reading
    reservation capacity and must use this session for every
    database operation.

    PyMongo's with_transaction handles its specified retry rules;
    do not layer uncontrolled application retries around it.
    """
    if not callable(operation):
        raise CafeReservationTransactionError(
            "A reservation transaction callback is required."
        )

    client = mongo.cx
    if client is None:
        raise CafeReservationTransactionError(
            "MongoDB transaction client is unavailable."
        )

    def transactional_callback(session):
        if session is None or getattr(
            session, "in_transaction", False
        ) is not True:
            raise CafeReservationTransactionError(
                "An active reservation transaction is required."
            )

        return operation(session)

    with client.start_session() as session:
        if session is None or not callable(
            getattr(session, "with_transaction", None)
        ):
            raise CafeReservationTransactionError(
                "MongoDB transaction session is unavailable."
            )

        return session.with_transaction(
            transactional_callback
        )
