"""HTTP API tests for Soultie public culture knowledge."""

import pytest
from bson import ObjectId
from flask import Flask

from app.api.v1 import api_v1_bp
from app.api.v1.culture import routes as culture_routes
from app.services import culture_service
from app.services.culture_service import (
    CultureNotFoundError,
)


@pytest.fixture
def app():
    application = Flask(
        __name__
    )

    application.config.update(
        TESTING=True,
    )

    application.register_blueprint(
        api_v1_bp
    )

    return application


@pytest.fixture
def client(
    app,
):
    return app.test_client()


def _public_entity():
    return {
        "_id": str(
            ObjectId()
        ),
        "entity_type": "community",
        "slug": "example-community",
        "name": "Example Community",
        "aliases": [],
        "summary": "Example summary.",
        "description": (
            "Example description."
        ),
        "regions": [
            "Assam",
        ],
        "languages": [
            "Assamese",
        ],
        "related_entities": [],
        "sources": [],
    }


def _public_festival():
    return {
        "_id": str(
            ObjectId()
        ),
        "slug": "example-festival",
        "name": "Example Festival",
        "aliases": [],
        "summary": "Example summary.",
        "description": (
            "Example description."
        ),
        "regions": [
            "Assam",
        ],
        "communities": [],
        "tribes": [],
        "languages": [
            "Assamese",
        ],
        "timing": {
            "date_type": "seasonal",
            "month": 4,
            "day": None,
            "date_text": (
                "Usually observed in April."
            ),
            "calendar_note": None,
        },
        "related_entities": [],
        "sources": [],
    }


def test_entity_list_uses_default_pagination(
    client,
    monkeypatch,
):
    captured = {}

    def fake_list(**kwargs):
        captured.update(
            kwargs
        )

        return [
            _public_entity()
        ]

    monkeypatch.setattr(
        culture_routes,
        "list_public_culture_entities",
        fake_list,
    )

    response = client.get(
        "/api/v1/culture/entities"
    )

    assert response.status_code == 200

    payload = response.get_json()

    assert len(
        payload[
            "entities"
        ]
    ) == 1

    assert payload[
        "pagination"
    ] == {
        "limit": 20,
        "offset": 0,
        "returned": 1,
    }

    assert captured == {
        "entity_type": None,
        "region": None,
        "language": None,
        "search": None,
        "limit": 20,
        "skip": 0,
    }


def test_entity_list_forwards_filters(
    client,
    monkeypatch,
):
    captured = {}

    def fake_list(**kwargs):
        captured.update(
            kwargs
        )

        return []

    monkeypatch.setattr(
        culture_routes,
        "list_public_culture_entities",
        fake_list,
    )

    response = client.get(
        (
            "/api/v1/culture/entities"
            "?type=community"
            "&region=Assam"
            "&language=Assamese"
            "&q=bihu"
            "&limit=10"
            "&offset=20"
        )
    )

    assert response.status_code == 200

    assert captured == {
        "entity_type": "community",
        "region": "Assam",
        "language": "Assamese",
        "search": "bihu",
        "limit": 10,
        "skip": 20,
    }


@pytest.mark.parametrize(
    (
        "query",
        "field",
    ),
    [
        (
            "?limit=0",
            "limit",
        ),
        (
            "?limit=101",
            "limit",
        ),
        (
            "?limit=abc",
            "limit",
        ),
        (
            "?offset=-1",
            "offset",
        ),
        (
            "?offset=abc",
            "offset",
        ),
    ],
)
def test_entity_list_rejects_invalid_pagination(
    client,
    query,
    field,
):
    response = client.get(
        (
            "/api/v1/culture/entities"
            + query
        )
    )

    assert response.status_code == 400

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "CULTURE_VALIDATION_ERROR"
    )

    assert (
        payload[
            "error"
        ][
            "field"
        ]
        == field
    )


def test_entity_list_rejects_long_search(
    client,
):
    response = client.get(
        (
            "/api/v1/culture/entities?q="
            + (
                "a"
                * 201
            )
        )
    )

    assert response.status_code == 400

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "field"
        ]
        == "q"
    )


def test_entity_detail_returns_entity(
    client,
    monkeypatch,
):
    expected = (
        _public_entity()
    )

    monkeypatch.setattr(
        culture_routes,
        "get_public_culture_entity",
        lambda entity_type, slug: (
            expected
        ),
    )

    response = client.get(
        (
            "/api/v1/culture/entities/"
            "community/example-community"
        )
    )

    assert response.status_code == 200

    assert (
        response.get_json()[
            "entity"
        ]
        == expected
    )


def test_entity_detail_returns_404(
    client,
    monkeypatch,
):
    def missing(
        entity_type,
        slug,
    ):
        raise CultureNotFoundError(
            "CULTURE_ENTITY_NOT_FOUND",
            "Culture entity was not found.",
        )

    monkeypatch.setattr(
        culture_routes,
        "get_public_culture_entity",
        missing,
    )

    response = client.get(
        (
            "/api/v1/culture/entities/"
            "community/missing"
        )
    )

    assert response.status_code == 404

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "CULTURE_ENTITY_NOT_FOUND"
    )


def test_invalid_entity_type_returns_400(
    client,
):
    response = client.get(
        (
            "/api/v1/culture/entities/"
            "unsupported/example"
        )
    )

    assert response.status_code == 400

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "CULTURE_VALIDATION_ERROR"
    )

    assert (
        payload[
            "error"
        ][
            "field"
        ]
        == "entity_type"
    )


def test_unpublished_entity_cannot_leak_through_api(
    client,
    monkeypatch,
):
    hidden = {
        "_id": ObjectId(),
        "entity_type": "community",
        "slug": "hidden-community",
        "name": "Hidden Community",
        "name_normalized": (
            "hidden community"
        ),
        "aliases": [],
        "summary": "Hidden.",
        "description": "Hidden.",
        "regions": [],
        "languages": [],
        "related_entities": [],
        "source_ids": [],
        "review_status": "approved",
        "published": False,
    }

    monkeypatch.setattr(
        culture_service,
        "get_culture_entity",
        lambda entity_type, slug: (
            hidden
        ),
    )

    response = client.get(
        (
            "/api/v1/culture/entities/"
            "community/hidden-community"
        )
    )

    assert response.status_code == 404

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "CULTURE_ENTITY_NOT_FOUND"
    )


def test_festival_list_uses_filters(
    client,
    monkeypatch,
):
    captured = {}

    def fake_list(**kwargs):
        captured.update(
            kwargs
        )

        return [
            _public_festival()
        ]

    monkeypatch.setattr(
        culture_routes,
        "list_public_festivals",
        fake_list,
    )

    response = client.get(
        (
            "/api/v1/culture/festivals"
            "?region=Assam"
            "&community=Example"
            "&tribe=ExampleTribe"
            "&language=Assamese"
            "&q=bihu"
            "&limit=15"
            "&offset=5"
        )
    )

    assert response.status_code == 200

    payload = response.get_json()

    assert (
        payload[
            "pagination"
        ][
            "returned"
        ]
        == 1
    )

    assert captured == {
        "region": "Assam",
        "community": "Example",
        "tribe": "ExampleTribe",
        "language": "Assamese",
        "search": "bihu",
        "limit": 15,
        "skip": 5,
    }


def test_festival_detail_returns_festival(
    client,
    monkeypatch,
):
    expected = (
        _public_festival()
    )

    monkeypatch.setattr(
        culture_routes,
        "get_public_festival",
        lambda slug: expected,
    )

    response = client.get(
        (
            "/api/v1/culture/festivals/"
            "example-festival"
        )
    )

    assert response.status_code == 200

    assert (
        response.get_json()[
            "festival"
        ]
        == expected
    )


def test_festival_detail_returns_404(
    client,
    monkeypatch,
):
    def missing(
        slug,
    ):
        raise CultureNotFoundError(
            "FESTIVAL_NOT_FOUND",
            "Festival was not found.",
        )

    monkeypatch.setattr(
        culture_routes,
        "get_public_festival",
        missing,
    )

    response = client.get(
        (
            "/api/v1/culture/festivals/"
            "missing-festival"
        )
    )

    assert response.status_code == 404

    payload = response.get_json()

    assert (
        payload[
            "error"
        ][
            "code"
        ]
        == "FESTIVAL_NOT_FOUND"
    )
