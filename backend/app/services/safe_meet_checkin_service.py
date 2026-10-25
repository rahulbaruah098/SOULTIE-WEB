"""Participant-controlled Safe Meet arrival/departure check-ins.

Does not collect location or notify contacts.
"""

from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ReturnDocument


class SafeMeetCheckinError(ValueError):
    pass


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SafeMeetCheckinError("Timezone-aware timestamp required.")
    return value.astimezone(timezone.utc)


def _id(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise SafeMeetCheckinError("Invalid identifier.")
    return str(ObjectId(value))


def ensure_safe_meet_checkin_indexes(database):
    database.safe_meet_checkins.create_index(
        [("safe_meet_id", 1), ("participant_id", 1)],
        unique=True,
        name="safe_meet_checkin_per_participant",
    )
    database.safe_meet_checkin_events.create_index(
        [("safe_meet_id", 1), ("participant_id", 1), ("sequence", 1)],
        unique=True,
        name="safe_meet_checkin_audit_sequence",
    )


def perform_safe_meet_checkin(
    *,
    database,
    safe_meet_id,
    participant_id,
    action,
    now,
):
    """Atomically record an explicit arrival or safe departure.

    Re-check the current meeting document under the same transaction.
    A write fence prevents concurrent lifecycle updates from
    committing without a MongoDB write conflict.
    """
    if database is None:
        raise SafeMeetCheckinError("Database required.")
    meet_id = _id(safe_meet_id)
    member = _id(participant_id)
    moment = _utc(now)
    if action not in ("arrived", "left_safely"):
        raise SafeMeetCheckinError("Unsupported check-in action.")

    with database.client.start_session() as session:
        with session.start_transaction():
            meeting = database.safe_meet_plans.find_one(
                {"_id": ObjectId(meet_id)},
                session=session,
            )
            if not isinstance(meeting, dict):
                raise SafeMeetCheckinError("Meeting not found.")
            if meeting.get("status") != "confirmed":
                raise SafeMeetCheckinError("Meeting is not active.")
            if meeting.get("booking_health") == "needs_attention":
                raise SafeMeetCheckinError("Booking requires attention.")

            participants = meeting.get("participant_ids")
            if not isinstance(participants, list) or member not in participants:
                raise SafeMeetCheckinError("Not a meeting participant.")

            revision = meeting.get("revision")
            if type(revision) is not int:
                raise SafeMeetCheckinError("Invalid meeting revision.")

            current = database.safe_meet_checkins.find_one(
                {"safe_meet_id": meet_id, "participant_id": member},
                session=session,
            )
            state = current.get("status") if current else None

            if action == "arrived" and state is not None:
                raise SafeMeetCheckinError("Arrival already recorded.")
            if action == "left_safely" and state != "arrived":
                raise SafeMeetCheckinError(
                    "Safe departure requires a prior arrival."
                )

            # Competes with cancellation/rescheduling writes to the plan.
            fence = database.safe_meet_plans.update_one(
                {
                    "_id": ObjectId(meet_id),
                    "status": "confirmed",
                    "revision": revision,
                },
                {"$inc": {"checkin_write_fence": 1}},
                session=session,
            )
            if fence.modified_count != 1:
                raise SafeMeetCheckinError("Meeting changed.")

            sequence = 1 if action == "arrived" else 2
            if action == "arrived":
                record = {
                    "safe_meet_id": meet_id,
                    "participant_id": member,
                    "status": "arrived",
                    "arrived_at": moment,
                    "left_safely_at": None,
                    "meeting_revision": revision,
                    "updated_at": moment,
                }
                database.safe_meet_checkins.insert_one(
                    record, session=session
                )
            else:
                result = database.safe_meet_checkins.update_one(
                    {
                        "safe_meet_id": meet_id,
                        "participant_id": member,
                        "status": "arrived",
                    },
                    {
                        "$set": {
                            "status": "left_safely",
                            "left_safely_at": moment,
                            "updated_at": moment,
                        }
                    },
                    session=session,
                )
                if result.modified_count != 1:
                    raise SafeMeetCheckinError("Departure state changed.")

            database.safe_meet_checkin_events.insert_one(
                {
                    "safe_meet_id": meet_id,
                    "participant_id": member,
                    "action": action,
                    "sequence": sequence,
                    "meeting_revision": revision,
                    "created_at": moment,
                },
                session=session,
            )

            return {
                "safe_meet_id": meet_id,
                "participant_id": member,
                "status": action,
                "recorded_at": moment.isoformat(),
                "sequence": sequence,
            }
