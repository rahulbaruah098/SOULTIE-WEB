"""Reject accidental private fields in match-created events."""

from app.schemas.match_experience import build_match_created_payload


def test_match_created_preserves_public_match_contract():
    match = {
        "id": "match-123",
        "other_user_id": "peer-456",
        "status": "active",
        "matched_at": "2026-10-26T10:00:00+00:00",
        "updated_at": "2026-10-26T10:00:00+00:00",
    }
    result = build_match_created_payload(match=match)
    assert result["event"] == "match.created"
    assert result["experience_available"] is True
    assert result["match"] == match


def test_match_created_discards_unapproved_fields():
    match = {
        "id": "match-123",
        "other_user_id": "peer-456",
        "status": "active",
        "phone": "PRIVATE_PHONE",
        "email": "PRIVATE_EMAIL",
        "location": {"lat": 1, "lng": 2},
        "culture_preferences": {"languages": ["private"]},
        "compatibility_score": 88,
        "trusted_contacts": ["PRIVATE_CONTACT"],
        "access_token": "PRIVATE_TOKEN",
    }
    result = build_match_created_payload(match=match)

    assert result["match"] == {
        "id": "match-123",
        "other_user_id": "peer-456",
        "status": "active",
    }
    assert "PRIVATE_PHONE" not in str(result)
    assert "PRIVATE_TOKEN" not in str(result)


def test_match_created_keeps_only_required_identity():
    result = build_match_created_payload(
        match={"id": "match-123", "other_user_id": "peer-456"}
    )
    assert result["match"] == {
        "id": "match-123",
        "other_user_id": "peer-456",
    }
