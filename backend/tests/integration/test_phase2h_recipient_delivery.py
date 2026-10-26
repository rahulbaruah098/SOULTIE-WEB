"""Isolated Phase 2H per-recipient notification behavior."""

from bson import ObjectId
from app.services import matching_service as matching


def test_each_match_member_receives_individual_payload(monkeypatch):
    first = ObjectId()
    second = ObjectId()
    match = {"_id": ObjectId(), "status": "active"}
    emitted = []

    def serialize(document, recipient):
        assert document is match
        peer = second if recipient == first else first
        return {
            "id": str(document["_id"]),
            "other_user_id": str(peer),
            "status": "active",
        }

    def emit(*, user_id, match):
        emitted.append((user_id, match))

    monkeypatch.setattr(matching, "serialize_match_for_user", serialize)
    monkeypatch.setattr(matching, "emit_match_created", emit)

    matching._emit_new_match_created_best_effort(
        match=match, first_user_id=first, second_user_id=second
    )

    assert len(emitted) == 2
    assert emitted[0][0] == first
    assert emitted[0][1]["other_user_id"] == str(second)
    assert emitted[1][0] == second
    assert emitted[1][1]["other_user_id"] == str(first)


def test_first_recipient_failure_does_not_block_second(monkeypatch):
    first = ObjectId()
    second = ObjectId()
    match = {"_id": ObjectId()}
    attempted = []

    def serialize(document, recipient):
        return {
            "id": str(document["_id"]),
            "other_user_id": str(second if recipient == first else first),
        }

    def emit(*, user_id, match):
        attempted.append(user_id)
        if user_id == first:
            raise RuntimeError("Simulated socket delivery failure")

    monkeypatch.setattr(matching, "serialize_match_for_user", serialize)
    monkeypatch.setattr(matching, "emit_match_created", emit)

    matching._emit_new_match_created_best_effort(
        match=match, first_user_id=first, second_user_id=second
    )

    assert attempted == [first, second]
