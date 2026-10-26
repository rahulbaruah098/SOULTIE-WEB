"""Phase 2O real blueprint auth-denial and route-registration acceptance.

Use an isolated Flask app to avoid touching live MongoDB.
"""

import pytest
from bson import ObjectId
from flask import Flask

from app.api.v1.reports.routes import reports_bp
from app.api.v1.report_moderation_routes import report_moderation_bp
from app.api.v1.report_appeal_routes import report_appeal_bp
from app.api.v1.user_block_routes import user_blocks_bp
from app.api.v1.account_moderation_routes import account_moderation_bp


@pytest.fixture
def client():
    """Use the actual nested API blueprint registered in production."""
    from app.api.v1 import api_v1_bp

    app = Flask("phase2o-api-acceptance")
    app.config.update(
        TESTING=True,
        SECRET_KEY="phase2o-test-only-secret",
        FRONTEND_ORIGIN="https://soultie.test",
    )
    app.register_blueprint(api_v1_bp)
    return app.test_client()



@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/reports"),
    ("GET", "/api/v1/reports"),
    ("POST", "/api/v1/report-appeals/reports/" + str(ObjectId())),
    ("GET", "/api/v1/blocks"),
    ("POST", "/api/v1/blocks/" + str(ObjectId())),
    ("DELETE", "/api/v1/blocks/" + str(ObjectId())),
    ("GET", "/api/v1/report-moderation/cases"),
    ("POST", "/api/v1/report-moderation/cases/" + str(ObjectId()) + "/decide"),
    ("GET", "/api/v1/report-appeals/admin"),
    ("POST", "/api/v1/report-appeals/admin/" + str(ObjectId()) + "/review"),
    ("POST", "/api/v1/account-moderation/users/" + str(ObjectId())),
])
def test_anonymous_requests_are_denied(client, method, path):
    response = client.open(
        path,
        method=method,
        json={"reason": "Test request"},
    )
    assert response.status_code in (401, 403), (
        method, path, response.status_code, response.get_data(as_text=True)
    )


def test_all_five_phase2o_blueprints_have_registered_routes(client):
    routes = {
        rule.rule for rule in client.application.url_map.iter_rules()
    }
    expected = {
        "/api/v1/reports",
        "/api/v1/report-moderation/cases",
        "/api/v1/report-appeals/admin",
        "/api/v1/blocks",
    }
    assert expected.issubset(routes)
    assert any(
        route.startswith("/api/v1/account-moderation/users/")
        for route in routes
    )