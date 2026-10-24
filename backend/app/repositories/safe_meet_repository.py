"""Transactional MongoDB persistence for Xuoroni Safe Meet.

Internal repository only. Authorization, conversation membership,
venue eligibility and reservation ownership must be checked by
the calling application service before these operations are used.
"""

from copy import deepcopy
from datetime import datetime

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.services.safe_meet_lifecycle_service import (
    SafeMeetLifecycleError,
    apply_safe_meet_action,
)


class SafeMeetRepositoryError(RuntimeError):
    """Safe Meet persistence operation failed."""


class SafeMeetConflictError(SafeMeetRepositoryError):
    """A plan was changed or was already created."""


class SafeMeetNotFoundError(SafeMeetRepositoryError):
    """No Safe Meet plan exists for the supplied identifier."""


def _database(db):
    if db is not None:
        return db
    return mongo.db


def _object_id(value):
    if isinstance(value, ObjectId):
        return value
    if type(value) is str and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError("Invalid Safe Meet identifier.")


def _validate_initial_plan(plan, event):
    if not isinstance(plan, dict) or not isinstance(event, dict):
        raise ValueError("Invalid Safe Meet proposal.")
    if plan.get("status") != "proposed":
        raise ValueError("Only proposals may be created.")
    if type(plan.get("revision")) is not int or plan["revision"] != 1:
        raise ValueError("Invalid initial revision.")
    if (
        event.get("action") != "propose"
        or event.get("revision") != 1
        or event.get("to_status") != "proposed"
        or event.get("actor_id") != plan.get("proposer_id")
    ):
        raise ValueError("Initial audit event does not match proposal.")


def create_safe_meet_record(plan, event, *, db=None):
    """Atomically create one plan and one audit event."""
    _validate_initial_plan(plan, event)
    database = _database(db)
    plan_doc = deepcopy(plan)
    event_doc = deepcopy(event)

    plan_id = _object_id(plan_doc.get("_id", ObjectId()))
    plan_doc["_id"] = plan_id

    event_doc["_id"] = ObjectId()
    event_doc["safe_meet_id"] = plan_id

    try:
        with database.client.start_session() as session:
            with session.start_transaction():
                database.safe_meet_plans.insert_one(
                    plan_doc,
                    session=session,
                )
                database.safe_meet_events.insert_one(
                    event_doc,
                    session=session,
                )
    except DuplicateKeyError as exc:
        raise SafeMeetConflictError(
            "Safe Meet proposal already exists."
        ) from exc

    return deepcopy(plan_doc), deepcopy(event_doc)


def get_safe_meet_record(safe_meet_id, *, db=None):
    """Read a stored plan; caller must check access beforehand."""
    database = _database(db)
    record = database.safe_meet_plans.find_one(
        {"_id": _object_id(safe_meet_id)}
    )
    if record is None:
        raise SafeMeetNotFoundError("Safe Meet plan not found.")
    return record


def transition_safe_meet_record(
    safe_meet_id,
    *,
    actor_id,
    action,
    expected_revision,
    now,
    db=None,
):
    """Atomically apply one lifecycle transition with audit data.

    Concurrent or stale writes cannot silently overwrite a plan.
    No audit event persists unless the matching plan update persists.
    """
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("Invalid expected revision.")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required.")

    database = _database(db)
    plan_id = _object_id(safe_meet_id)

    with database.client.start_session() as session:
        with session.start_transaction():
            original = database.safe_meet_plans.find_one(
                {"_id": plan_id},
                session=session,
            )

            if original is None:
                raise SafeMeetNotFoundError(
                    "Safe Meet plan not found."
                )

            if original.get("revision") != expected_revision:
                raise SafeMeetConflictError(
                    "Safe Meet plan has changed. Refresh and retry."
                )

            updated, event = apply_safe_meet_action(
                original,
                actor_id=actor_id,
                action=action,
                now=now,
            )

            updated["_id"] = plan_id

            # Phase 2M.4: consent and linked-reservation validation
            # must participate in the SAME MongoDB transaction.
            if action == "accept" and original.get("reservation_id"):
                from datetime import datetime as _sm_datetime
                from datetime import timezone as _sm_timezone

                try:
                    _sm_booking_id = _object_id(
                        original["reservation_id"]
                    )
                    _sm_venue_id = _object_id(original["venue_id"])
                    _sm_owner_id = _object_id(original["proposer_id"])
                    _sm_start = _sm_datetime.fromisoformat(
                        original["planned_at"]
                    )
                    if _sm_start.tzinfo is None:
                        raise ValueError("Timezone missing.")
                    _sm_start = _sm_start.astimezone(_sm_timezone.utc)
                    # BSON dates store milliseconds, not microseconds.
                    _sm_start = _sm_start.replace(
                        microsecond=(_sm_start.microsecond // 1000) * 1000
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SafeMeetConflictError(
                        "Linked booking metadata is invalid."
                    ) from exc

                # This real write acts as a concurrency fence.
                # It never changes the customer's booking status.
                _sm_guarded = database.reservations.update_one(
                    {
                        "_id": _sm_booking_id,
                        "customer_id": _sm_owner_id,
                        "venue_id": _sm_venue_id,
                        "status": "confirmed",
                        "start_at": _sm_start,
                        "end_at": {"$gt": _sm_start},
                        "party_size": {"$gte": 2},
                    },
                    {"$inc": {"safe_meet_consistency_revision": 1}},
                    session=session,
                )
                if _sm_guarded.matched_count != 1:
                    raise SafeMeetConflictError(
                        "Linked reservation changed. Refresh and retry."
                    )

            result = database.safe_meet_plans.replace_one(
                {
                    "_id": plan_id,
                    "revision": expected_revision,
                },
                updated,
                session=session,
            )

            if result.matched_count != 1:
                raise SafeMeetConflictError(
                    "Safe Meet plan changed concurrently."
                )

            event_doc = deepcopy(event)
            event_doc["_id"] = ObjectId()
            event_doc["safe_meet_id"] = plan_id

            database.safe_meet_events.insert_one(
                event_doc,
                session=session,
            )

    return updated, event_doc


def list_safe_meet_events(safe_meet_id, *, db=None):
    """Read audit events in revision order; access check is external."""
    database = _database(db)
    return list(
        database.safe_meet_events.find(
            {"safe_meet_id": _object_id(safe_meet_id)}
        ).sort([("revision", 1), ("_id", 1)])
    )