"""Caf? Superadmin authorization boundary tests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bson import ObjectId
from flask import Flask, session

from app.security import cafe_superadmin as security


@pytest.fixture
def guard_context(monkeypatch):
    app = Flask("cafe-superadmin-security-tests")
    app.config.update(
        TESTING=True,
        SECRET_KEY="local-only-test-session-key",
    )

    admin_id = ObjectId()
    collection = Mock()

    monkeypatch.setattr(
        security,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(admins=collection),
        ),
    )

    @security.require_cafe_superadmin
    def protected():
        return {"authorized": True}

    return app, admin_id, collection, protected


def test_anonymous_request_rejected(guard_context):
    app, _admin_id, collection, protected = guard_context

    with app.test_request_context("/"):
        response, code = protected()

    assert code == 401
    assert "authentication" in response.get_json()["error"].lower()
    collection.find_one.assert_not_called()


@pytest.mark.parametrize(
    "identity",
    ["invalid-id", "", 1234, None],
)
def test_invalid_session_identity_rejected(
    guard_context, identity
):
    app, _admin_id, collection, protected = guard_context

    with app.test_request_context("/"):
        session["admin_id"] = identity
        response, code = protected()

    assert code == 401
    assert response.get_json()["error"]
    collection.find_one.assert_not_called()


def test_deleted_admin_rejected(guard_context):
    app, admin_id, collection, protected = guard_context
    collection.find_one.return_value = None

    with app.test_request_context("/"):
        session["admin_id"] = str(admin_id)
        response, code = protected()

    assert code == 401
    collection.find_one.assert_called_once_with({
        "_id": admin_id
    })


@pytest.mark.parametrize(
    "admin_record",
    [
        {"role": "admin", "status": "active"},
        {"status": "active"},
        {"role": "employee", "status": "active"},
        {
            "role": "super_admin",
            "status": "active",
            "is_active": False,
        },
        {"role": "super_admin", "status": "suspended"},
        {"role": "super_admin", "status": "disabled"},
    ],
)
def test_unprivileged_or_inactive_admin_rejected(
    guard_context, admin_record
):
    app, admin_id, collection, protected = guard_context
    collection.find_one.return_value = {
        "_id": admin_id,
        **admin_record,
    }

    with app.test_request_context("/"):
        session["admin_id"] = str(admin_id)
        response, code = protected()

    assert code == 403
    assert response.get_json()["error"]


def test_active_superadmin_authorized(guard_context):
    app, admin_id, collection, protected = guard_context

    collection.find_one.return_value = {
        "_id": admin_id,
        "role": "super_admin",
        "status": "active",
        "is_active": True,
    }

    with app.test_request_context("/"):
        session["admin_id"] = str(admin_id)
        result = protected()

    assert result == {"authorized": True}
    collection.find_one.assert_called_once_with({
        "_id": admin_id
    })


def test_approval_is_rechecked_on_every_request(
    guard_context
):
    app, admin_id, collection, protected = guard_context

    collection.find_one.side_effect = [
        {
            "_id": admin_id,
            "role": "super_admin",
            "status": "active",
        },
        {
            "_id": admin_id,
            "role": "admin",
            "status": "active",
        },
    ]

    with app.test_request_context("/"):
        session["admin_id"] = str(admin_id)

        assert protected() == {"authorized": True}

        response, code = protected()

        assert code == 403
        assert response.get_json()["error"]

    assert collection.find_one.call_count == 2
