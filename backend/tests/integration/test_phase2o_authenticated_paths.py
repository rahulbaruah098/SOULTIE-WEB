"""Authenticated Phase 2O API success-path acceptance.

Mocks isolate real routing and authorization from production data.
"""

from unittest.mock import patch
from bson import ObjectId
from flask import Flask
import pytest

from app.api.v1 import api_v1_bp


@pytest.fixture
def environment(monkeypatch):
    import app.security.authentication as authentication
    import app.security.cafe_superadmin as superadmin

    user_id = ObjectId()
    admin_id = ObjectId()

    def authenticate(_token):
        return {
            "user": {
                "_id": user_id,
                "account_status": "active",
            },
            "session": {
                "_id": ObjectId(),
                "user_id": user_id,
            },
            "payload": {
                "sub": str(user_id),
            },
        }

    monkeypatch.setattr(
        authentication,
        "authenticate_access_token",
        authenticate,
    )

    class Admins:
        def find_one(self, selector):
            if selector.get("_id") == admin_id:
                return {
                    "_id": admin_id,
                    "role": "super_admin",
                    "status": "active",
                    "is_active": True,
                }
            return None

    class DB:
        admins = Admins()

    class Mongo:
        db = DB()

    monkeypatch.setattr(superadmin, "mongo", Mongo())

    app = Flask("phase2o-authenticated-acceptance")
    app.config.update(
        TESTING=True,
        SECRET_KEY="phase2o-local-test-secret",
        FRONTEND_ORIGIN="https://soultie.test",
    )
    app.register_blueprint(api_v1_bp)

    client = app.test_client()
    headers = {"Authorization": "Bearer test-access-token"}

    return client, app, user_id, admin_id, headers


def test_authenticated_report_submission(environment):
    client, _, user, _, headers = environment
    from app.api.v1.reports import routes

    with patch.object(routes, "create_report") as create:
        create.return_value = {
            "created": True,
            "report": {"id": str(ObjectId())},
        }
        response = client.post(
            "/api/v1/reports",
            json={"reason": "spam"},
            headers=headers,
        )

    assert response.status_code == 201
    assert create.call_args.args[0] == user


def test_authenticated_block_and_unblock(environment):
    client, _, user, _, headers = environment
    import app.api.v1.user_block_routes as routes

    target_id = str(ObjectId())

    with patch.object(routes, "block_user") as block:
        block.return_value = {
            "blocked": True,
            "blocked_user_id": target_id,
        }
        response = client.post(
            "/api/v1/blocks/" + target_id,
            headers=headers,
        )
        assert response.status_code == 200
        assert block.call_args.args == (user, target_id)

    with patch.object(routes, "unblock_user") as unblock:
        unblock.return_value = {
            "blocked": False,
            "blocked_user_id": target_id,
        }
        response = client.delete(
            "/api/v1/blocks/" + target_id,
            headers=headers,
        )
        assert response.status_code == 200
        assert unblock.call_args.args == (user, target_id)


def test_authenticated_appeal_submission(environment):
    client, _, user, _, headers = environment
    import app.api.v1.report_appeal_routes as routes
    report_id = str(ObjectId())

    with patch.object(routes, "submit_appeal") as submit:
        submit.return_value = {
            "created": True,
            "appeal": {"id": str(ObjectId())},
        }
        response = client.post(
            "/api/v1/report-appeals/reports/" + report_id,
            json={"reason": "Additional evidence for my report"},
            headers=headers,
        )

    assert response.status_code == 201
    assert submit.call_args.args[0] == user
    assert submit.call_args.args[1] == report_id


def test_superadmin_case_queue(environment):
    client, _, _, admin_id, _ = environment
    import app.api.v1.report_moderation_routes as routes

    with client.session_transaction() as session:
        session["admin_id"] = str(admin_id)

    with patch.object(routes, "list_cases") as queue:
        queue.return_value = {"cases": [], "next_cursor": None}
        response = client.get(
            "/api/v1/report-moderation/cases",
        )

    assert response.status_code == 200
    assert response.get_json()["cases"] == []


def test_superadmin_account_moderation(environment):
    client, _, _, admin_id, _ = environment
    import app.api.v1.account_moderation_routes as routes
    target_id = str(ObjectId())

    with client.session_transaction() as session:
        session["admin_id"] = str(admin_id)

    with patch.object(routes, "moderate_account") as moderate:
        moderate.return_value = {
            "user_id": target_id,
            "account_status": "suspended",
        }
        response = client.post(
            "/api/v1/account-moderation/users/" + target_id,
            json={
                "expected_status": "active",
                "decision": "suspended",
                "reason": "Repeated confirmed policy violation",
            },
            headers={"Origin": "https://soultie.test"},
        )

    assert response.status_code == 200
    assert moderate.call_args.kwargs["admin_id"] == str(admin_id)
    assert moderate.call_args.kwargs["user_id"] == target_id


def test_superadmin_wrong_origin_is_rejected(environment):
    client, _, _, admin_id, _ = environment

    with client.session_transaction() as session:
        session["admin_id"] = str(admin_id)

    response = client.post(
        "/api/v1/account-moderation/users/" + str(ObjectId()),
        json={
            "expected_status": "active",
            "decision": "banned",
            "reason": "Repeated confirmed serious misconduct",
        },
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 403


def test_ordinary_user_cannot_access_superadmin_queue(environment):
    client, _, _, _, headers = environment

    response = client.get(
        "/api/v1/report-moderation/cases",
        headers=headers,
    )
    assert response.status_code in (401, 403)
