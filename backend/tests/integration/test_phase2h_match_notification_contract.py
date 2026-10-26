"""Phase 2H match-created notification contract regression."""

import ast
import inspect

from app.schemas.match_experience import build_match_created_payload
from app.services import matching_service
from app.realtime import notifications


def test_match_created_payload_is_minimal():
    match = {
        "id": "507f1f77bcf86cd799439011",
        "other_user_id": "507f191e810c19729de860ea",
    }
    payload = build_match_created_payload(match=match)

    assert payload["event"] == "match.created"
    assert payload["experience_available"] is True
    assert payload["match"] == match
    assert set(payload) == {
        "event", "experience_available", "match"
    }


def test_match_created_payload_does_not_add_private_data():
    match = {
        "id": "507f1f77bcf86cd799439011",
        "other_user_id": "507f191e810c19729de860ea",
    }
    payload = build_match_created_payload(match=match)
    serialized = str(payload).lower()

    for private_key in (
        "phone", "email", "location", "compatibility_score",
        "culture_preferences", "trusted_contacts",
        "icebreakers", "access_token",
    ):
        assert private_key not in serialized


def test_match_created_payload_rejects_incomplete_identity():
    for invalid in (
        None, [], {},
        {"id": "one"},
        {"other_user_id": "two"},
    ):
        try:
            build_match_created_payload(match=invalid)
        except ValueError:
            continue
        raise AssertionError("Invalid match payload accepted")


def test_matching_service_has_notification_trigger():
    source = inspect.getsource(matching_service)
    tree = ast.parse(source)

    names = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "_emit_new_match_created_best_effort" in names
    function = names["_emit_new_match_created_best_effort"]

    calls = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "emit_match_created"
    ]
    assert calls, "Matching service no longer emits match.created"


def test_matching_action_calls_notification_hook():
    source = inspect.getsource(matching_service)
    tree = ast.parse(source)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_emit_new_match_created_best_effort"
    ]

    assert calls, "New-match action lost notification hook"


def test_realtime_uses_shared_privacy_payload():
    source = inspect.getsource(notifications.emit_match_created)
    tree = ast.parse(source)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_match_created_payload"
    ]

    assert calls, "Realtime emitter bypasses shared payload builder"
