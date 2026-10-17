"""Isolated Flask tests for protected cafe moderation API."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId
from flask import Flask
from pymongo.errors import PyMongoError

from app.api.v1 import cafe_admin_routes as routes
from app.security import cafe_superadmin as security
from app.services.cafe_moderation_service import (
    CafeModerationConflict,
    CafeModerationError,
)


@pytest.fixture
def api_context(monkeypatch):
    app = Flask("xuoroni-cafe-admin-api-tests")
    app.config.update(
        TESTING=True,
        SECRET_KEY="isolated-api-test-key",
        FRONTEND_ORIGIN="https://xuoroni.example",
    )
    app.register_blueprint(
        routes.cafe_admin_bp,
        url_prefix="/api/v1/cafe-admin",
    )

    admin_id = ObjectId()
    venue_id = ObjectId()
    admins = Mock()
    admins.find_one.return_value = {
        "_id": admin_id,
        "role": "super_admin",
        "status": "active",
        "is_active": True,
    }
    monkeypatch.setattr(
        security,
        "mongo",
        SimpleNamespace(db=SimpleNamespace(admins=admins)),
    )

    moderate = Mock(return_value={
        "venue_id": str(venue_id),
        "partner_id": str(ObjectId()),
        "previous_status": "pending",
        "status": "approved",
    })
    monkeypatch.setattr(routes, "moderate_cafe_venue", moderate)

    client = app.test_client()
    path = f"/api/v1/cafe-admin/venues/{venue_id}/moderate"

    def login():
        with client.session_transaction() as session:
            session["admin_id"] = str(admin_id)

    def post(
        *,
        origin="https://xuoroni.example",
        body=None,
        headers=None,
    ):
        supplied_headers = dict(headers or {})
        if origin is not None:
            supplied_headers["Origin"] = origin
        if body is None:
            body = {
                "expected_status": "pending",
                "decision": "approved",
            }
        return client.post(
            path,
            json=body,
            headers=supplied_headers,
        )

    return SimpleNamespace(
        app=app,
        admin_id=admin_id,
        venue_id=venue_id,
        admins=admins,
        moderate=moderate,
        client=client,
        path=path,
        login=login,
        post=post,
    )


def test_anonymous_admin_request_rejected(api_context):
    ctx = api_context
    response = ctx.post()
    assert response.status_code == 401
    ctx.moderate.assert_not_called()


@pytest.mark.parametrize("role", ["admin", "employee", None])
def test_non_superadmin_rejected(api_context, role):
    ctx = api_context
    ctx.login()
    ctx.admins.find_one.return_value = {
        "_id": ctx.admin_id,
        "role": role,
        "status": "active",
    }
    response = ctx.post()
    assert response.status_code == 403
    ctx.moderate.assert_not_called()


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "https://attacker.example",
        "https://xuoroni.example.attacker.example",
        "http://xuoroni.example",
        "https://xuoroni.example/",
        "null",
    ],
)
def test_untrusted_origin_rejected(api_context, origin):
    ctx = api_context
    ctx.login()
    response = ctx.post(origin=origin)
    assert response.status_code == 403
    ctx.moderate.assert_not_called()


def test_non_json_request_rejected(api_context):
    ctx = api_context
    ctx.login()
    response = ctx.client.post(
        ctx.path,
        data="not-json",
        headers={"Origin": "https://xuoroni.example"},
        content_type="text/plain",
    )
    assert response.status_code == 415
    ctx.moderate.assert_not_called()


def test_invalid_json_request_rejected(api_context):
    ctx = api_context
    ctx.login()
    response = ctx.client.post(
        ctx.path,
        data="{invalid",
        headers={"Origin": "https://xuoroni.example"},
        content_type="application/json",
    )
    assert response.status_code == 400
    ctx.moderate.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"expected_status": "pending"},
        {"decision": "approved"},
        {"expected_status": 123, "decision": "approved"},
        {
            "expected_status": "pending",
            "decision": "approved",
            "superadmin_id": "forged",
        },
        {"expected_status": "pending", "decision": "approved", "extra": 1},
    ],
)
def test_invalid_body_rejected(api_context, body):
    ctx = api_context
    ctx.login()
    response = ctx.post(body=body)
    assert response.status_code == 400
    ctx.moderate.assert_not_called()


def test_success_uses_authenticated_admin_identity(api_context):
    ctx = api_context
    ctx.login()
    response = ctx.post(
        body={
            "expected_status": "pending",
            "decision": "approved",
            "reason": None,
        }
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "approved"
    assert response.headers["Cache-Control"] == "no-store"
    ctx.moderate.assert_called_once_with(
        superadmin_id=str(ctx.admin_id),
        venue_id=str(ctx.venue_id),
        expected_status="pending",
        decision="approved",
        reason=None,
    )


def test_stale_decision_returns_conflict(api_context):
    ctx = api_context
    ctx.login()
    ctx.moderate.side_effect = CafeModerationConflict("Stale venue")

    response = ctx.post()

    assert response.status_code == 409
    ctx.moderate.assert_called_once()


def test_invalid_transition_returns_bad_request(api_context):
    ctx = api_context
    ctx.login()
    ctx.moderate.side_effect = CafeModerationError("Invalid transition")

    response = ctx.post()

    assert response.status_code == 400


def test_mongodb_failure_returns_unavailable(api_context):
    ctx = api_context
    ctx.login()
    ctx.moderate.side_effect = PyMongoError("Transaction unavailable")

    response = ctx.post()

    assert response.status_code == 503
    assert "unavailable" in response.get_json()["error"].lower()


# Phase 2J: permanent Superadmin cafe application listing tests.

class FakeCafeListingCursor:
    def __init__(self, documents):
        self.documents = list(documents)
        self.sort_calls = []
        self.limit_calls = []

    def sort(self, field, direction):
        self.sort_calls.append((field, direction))
        return self

    def limit(self, count):
        self.limit_calls.append(count)
        return self

    def __iter__(self):
        count = self.limit_calls[-1] if self.limit_calls else None
        return iter(self.documents[:count])


@pytest.fixture
def listing_context(api_context, monkeypatch):
    from datetime import datetime, timezone
    from app import extensions

    ctx = api_context
    first = ObjectId()
    second = ObjectId()
    third = ObjectId()

    documents = [
        {
            "_id": first,
            "owner_id": str(ObjectId()),
            "name": "Cafe One",
            "city": "Guwahati",
            "address": "Central Guwahati",
            "status": "pending",
            "created_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
            "password_hash": "NEVER_RETURN_THIS",
            "internal_notes": "PRIVATE_INTERNAL_DATA",
        },
        {
            "_id": second,
            "name": "Cafe Two",
            "status": "pending",
        },
        {
            "_id": third,
            "name": "Cafe Three",
            "status": "pending",
        },
    ]

    cursor = FakeCafeListingCursor(documents)
    venue_collection = Mock()
    venue_collection.find.return_value = cursor

    monkeypatch.setattr(
        extensions,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(venues=venue_collection)
        ),
    )

    return SimpleNamespace(
        api=ctx,
        ids=[first, second, third],
        cursor=cursor,
        venues=venue_collection,
        documents=documents,
        path="/api/v1/cafe-admin/venues",
    )


def test_listing_requires_admin_session(listing_context):
    ctx = listing_context
    response = ctx.api.client.get(ctx.path)

    assert response.status_code == 401
    ctx.venues.find.assert_not_called()


@pytest.mark.parametrize("role", ["admin", "employee", None])
def test_listing_rejects_non_superadmin(listing_context, role):
    ctx = listing_context
    ctx.api.login()
    ctx.api.admins.find_one.return_value = {
        "_id": ctx.api.admin_id,
        "role": role,
        "status": "active",
    }

    response = ctx.api.client.get(ctx.path)

    assert response.status_code == 403
    ctx.venues.find.assert_not_called()


def test_listing_default_status_and_private_fields(listing_context):
    ctx = listing_context
    ctx.api.login()

    response = ctx.api.client.get(ctx.path)

    assert response.status_code == 200
    result = response.get_json()
    assert len(result["items"]) == 3
    assert result["has_more"] is False
    assert result["next_cursor"] is None
    assert result["items"][0]["name"] == "Cafe One"
    assert result["items"][0]["created_at"].startswith("2026-10-16")
    assert response.headers["Cache-Control"] == "no-store"

    serialized = response.get_data(as_text=True)
    assert "NEVER_RETURN_THIS" not in serialized
    assert "PRIVATE_INTERNAL_DATA" not in serialized

    query, projection = ctx.venues.find.call_args.args
    assert query == {"status": "pending"}
    assert projection["name"] == 1
    assert "password_hash" not in projection
    assert "internal_notes" not in projection
    assert ctx.cursor.sort_calls == [("_id", -1)]
    assert ctx.cursor.limit_calls == [26]


@pytest.mark.parametrize(
    "status",
    ["pending", "approved", "rejected", "suspended"],
)
def test_listing_allows_known_status_filter(listing_context, status):
    ctx = listing_context
    ctx.api.login()

    response = ctx.api.client.get(
        ctx.path,
        query_string={"status": status, "limit": "3"},
    )

    assert response.status_code == 200
    query = ctx.venues.find.call_args.args[0]
    assert query["status"] == status
    assert ctx.cursor.limit_calls == [4]


@pytest.mark.parametrize(
    "parameters",
    [
        {"status": "unknown"},
        {"status": ""},
        {"limit": "0"},
        {"limit": "51"},
        {"limit": "-1"},
        {"limit": "3.5"},
        {"limit": "abc"},
        {"limit": "001"},
        {"after_id": "not-an-object-id"},
    ],
)
def test_listing_rejects_invalid_filters(
    listing_context, parameters
):
    ctx = listing_context
    ctx.api.login()

    response = ctx.api.client.get(
        ctx.path,
        query_string=parameters,
    )

    assert response.status_code == 400
    ctx.venues.find.assert_not_called()


def test_listing_returns_next_cursor_when_more_exist(listing_context):
    ctx = listing_context
    ctx.api.login()

    response = ctx.api.client.get(
        ctx.path,
        query_string={"limit": "2"},
    )

    assert response.status_code == 200
    result = response.get_json()
    assert len(result["items"]) == 2
    assert result["has_more"] is True
    assert result["next_cursor"] == str(ctx.ids[1])
    assert ctx.cursor.limit_calls == [3]


def test_listing_applies_object_id_cursor(listing_context):
    ctx = listing_context
    ctx.api.login()

    response = ctx.api.client.get(
        ctx.path,
        query_string={"after_id": str(ctx.ids[1])},
    )

    assert response.status_code == 200
    query = ctx.venues.find.call_args.args[0]
    assert query["_id"] == {"$lt": ctx.ids[1]}


def test_listing_database_failure_returns_unavailable(listing_context):
    ctx = listing_context
    ctx.api.login()
    ctx.venues.find.side_effect = PyMongoError("Test database unavailable")

    response = ctx.api.client.get(ctx.path)

    assert response.status_code == 503
    assert "unavailable" in response.get_json()["error"].lower()
