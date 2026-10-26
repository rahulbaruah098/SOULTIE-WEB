"""Internal transactional cafe booking orchestration for Soultie.

Do NOT register this module directly as an HTTP endpoint.

The application integration layer must supply trusted server-owned
functions for customer authorization and venue booking policy. Both
run inside the active MongoDB transaction on every retry.

The policy function must independently enforce approved/active venue,
booking settings, venue timezone, operating hours, advance limits,
party-size rules and any booking blackout restrictions.

No notifications or other external side effects are emitted here.
The database-backed policy adapter, durable outbox, real MongoDB
concurrency tests and public routes remain separate unfinished work.
"""

from datetime import timedelta

from bson import ObjectId

from pymongo.errors import DuplicateKeyError, PyMongoError

from app.services.cafe_booking_outcome_reconciliation_service import (
    reconcile_existing_cafe_booking,
)

from app.repositories.cafe_booking_outbox_repository import (
    record_cafe_booking_event,
)

from app.schemas.reservation import validate_reservation_request
from app.repositories.cafe_reservation_fence_repository import (
    acquire_venue_reservation_fence,
)
from app.repositories.reservation_repository import (
    find_reservation_by_idempotency_key,
    find_overlapping_active_reservations,
    insert_pending_cafe_reservation,
)
from app.services.cafe_reservation_capacity_service import (
    evaluate_reservation_capacity,
)
from app.services.cafe_reservation_idempotency_service import (
    resolve_existing_reservation_replay,
)
from app.security.cafe_booking_customer_access import (
    authorize_cafe_booking_customer,
)
from app.services.cafe_booking_policy_service import (
    load_cafe_booking_policy,
)
from app.services.cafe_reservation_transaction_service import (
    run_cafe_reservation_transaction,
)


class CafeBookingError(ValueError):
    """Booking cannot be safely created."""


class CafeBookingCapacityConflict(CafeBookingError):
    """Insufficient available venue booking capacity."""


def _integer(value, name, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise CafeBookingError("Invalid " + name + ".")
    return value


def _trusted_policy(policy, venue_id):
    """Require an explicit, server-validated booking policy."""
    if not isinstance(policy, dict):
        raise CafeBookingError("Booking policy is unavailable.")

    expected = {
        "venue_id",
        "booking_allowed",
        "seat_capacity",
        "duration_minutes",
        "buffer_minutes",
        "max_concurrent_bookings",
    }
    if set(policy) != expected:
        raise CafeBookingError("Incomplete booking policy.")

    if (
        policy["venue_id"] != venue_id
        or policy["booking_allowed"] is not True
    ):
        raise CafeBookingError("Venue is not bookable.")

    capacity = _integer(
        policy["seat_capacity"], "seat capacity", 1, 10000
    )
    duration = _integer(
        policy["duration_minutes"], "booking duration", 15, 720
    )
    buffer = _integer(
        policy["buffer_minutes"], "booking buffer", 0, 180
    )

    maximum = policy["max_concurrent_bookings"]
    if maximum is not None:
        maximum = _integer(
            maximum, "concurrent booking limit", 1, 10000
        )

    return {
        "seat_capacity": capacity,
        "duration_minutes": duration,
        "buffer_minutes": buffer,
        "max_concurrent_bookings": maximum,
    }


def create_pending_cafe_booking(
    *,
    customer_id,
    request_data,
    authorize_customer,
    load_booking_policy,
):
    """Create a pending booking through the full transaction boundary.

    authorize_customer(customer_id, venue_id, session) must return
    exactly True for an authenticated, eligible customer.

    load_booking_policy(venue_id, cleaned_request, session) must
    return explicit server-validated policy values. It must reject
    closed venues and out-of-hours or invalid booking requests.

    Never pass client-created callbacks to this function.
    """
    if (
        not isinstance(customer_id, str)
        or not ObjectId.is_valid(customer_id)
    ):
        raise CafeBookingError("Invalid customer identity.")

    if (
        not callable(authorize_customer)
        or not callable(load_booking_policy)
    ):
        raise CafeBookingError(
            "Trusted booking authorization and policy are required."
        )

    cleaned = validate_reservation_request(request_data)
    venue_id = cleaned["venue_id"]
    customer_id = str(ObjectId(customer_id))

    def operation(session):
        if (
            authorize_customer(customer_id, venue_id, session)
            is not True
        ):
            raise CafeBookingError(
                "Customer is not authorized to book this venue."
            )

        acquire_venue_reservation_fence(
            venue_id=venue_id,
            session=session,
        )

        rules = _trusted_policy(
            load_booking_policy(venue_id, cleaned, session),
            venue_id,
        )

        existing = find_reservation_by_idempotency_key(
            idempotency_key=cleaned["idempotency_key"],
            session=session,
        )

        replay = resolve_existing_reservation_replay(
            existing=existing,
            customer_id=customer_id,
            request_data=request_data,
            duration_minutes=rules["duration_minutes"],
            buffer_minutes=rules["buffer_minutes"],
        )
        if replay is not None:
            return replay

        end_at = cleaned["start_at"] + timedelta(
            minutes=(
                rules["duration_minutes"]
                + rules["buffer_minutes"]
            )
        )

        reservations = find_overlapping_active_reservations(
            venue_id=venue_id,
            start_at=cleaned["start_at"],
            end_at=end_at,
            session=session,
        )

        decision = evaluate_reservation_capacity(
            start_at=cleaned["start_at"],
            party_size=cleaned["party_size"],
            duration_minutes=rules["duration_minutes"],
            buffer_minutes=rules["buffer_minutes"],
            seat_capacity=rules["seat_capacity"],
            reservations=reservations,
            max_concurrent_bookings=(
                rules["max_concurrent_bookings"]
            ),
        )

        if (
            not isinstance(decision, dict)
            or decision.get("available") is not True
        ):
            raise CafeBookingCapacityConflict(
                "Booking capacity is unavailable."
            )

        inserted = insert_pending_cafe_reservation(
            customer_id=customer_id,
            request_data=request_data,
            duration_minutes=rules["duration_minutes"],
            buffer_minutes=rules["buffer_minutes"],
            session=session,
        )

        if (
            not isinstance(inserted, dict)
            or inserted.get("status") != "pending"
            or not isinstance(
                inserted.get("reservation_id"), str
            )
            or not ObjectId.is_valid(
                inserted["reservation_id"]
            )
        ):
            raise CafeBookingError(
                "Reservation persistence acknowledgement failed."
            )

        # The outbox insert must succeed in the SAME transaction
        # as the reservation insert. Any error aborts the booking.
        # Idempotency replays return earlier and never emit again.
        record_cafe_booking_event(
            reservation_id=inserted["reservation_id"],
            venue_id=venue_id,
            customer_id=customer_id,
            event_type="booking_created",
            reservation_status="pending",
            session=session,
        )

        return {**inserted, "replayed": False}

    try:
        return run_cafe_reservation_transaction(
            operation=operation
        )
    except DuplicateKeyError:
        # The transaction has ended. A duplicate index failure is
        # not proof that the requested booking already exists.
        # Only a verified exact prior booking can be returned.
        return reconcile_existing_cafe_booking(
            customer_id=customer_id,
            request_data=request_data,
        )
    except PyMongoError as exc:
        # PyMongo performs its documented commit retries. Do not
        # retry the full booking transaction at application level.
        # Only uncertain commit outcomes qualify for reconciliation.
        if not exc.has_error_label("UnknownTransactionCommitResult"):
            raise
        return reconcile_existing_cafe_booking(
            customer_id=customer_id,
            request_data=request_data,
        )


def create_pending_cafe_booking_with_live_policy(
    *,
    customer_id,
    request_data,
    authorize_customer,
):
    """Server-only booking entry point using the real venue policy.

    Customer authorization is mandatory and must be supplied by
    trusted application code, never by an HTTP caller.

    The booking coordinator acquires the venue fence before
    invoking the database-backed policy loader.

    Do not expose this function through a public route until
    customer authentication, configuration writes, outbox and
    real concurrency testing are completed.
    """
    if not callable(authorize_customer):
        raise CafeBookingError(
            "Trusted customer authorization is required."
        )

    return create_pending_cafe_booking(
        customer_id=customer_id,
        request_data=request_data,
        authorize_customer=authorize_customer,
        load_booking_policy=load_cafe_booking_policy,
    )


def create_pending_cafe_booking_with_live_access(
    *,
    customer_id,
    request_data,
):
    """Internal booking entry point using trusted database adapters.

    Only call this with customer_id obtained from a verified dating-user
    authentication context. It does NOT authenticate an HTTP request.

    Customer eligibility, venue policy and capacity are rechecked inside
    the booking transaction. The venue fence protects the capacity read.

    Do not expose bookings publicly until booking-sensitive configuration,
    durable notification outbox, duplicate-key race handling and real
    MongoDB concurrency testing are completed.
    """
    return create_pending_cafe_booking(
        customer_id=customer_id,
        request_data=request_data,
        authorize_customer=authorize_cafe_booking_customer,
        load_booking_policy=load_cafe_booking_policy,
    )
