"""Business-rule tests for Soultie culture knowledge."""

from copy import deepcopy

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.services import culture_service
from app.services.culture_service import (
    CultureConflictError,
    CultureNotFoundError,
    CulturePublicationError,
    create_culture_knowledge_entity,
    get_public_culture_entity,
    list_public_culture_entities,
    serialize_culture_entity_public,
    update_festival_knowledge,
)


def _source(
    *,
    active=True,
):
    return {
        "_id": ObjectId(),
        "source_url": (
            "https://example.com/source"
        ),
        "source_url_normalized": (
            "https://example.com/source"
        ),
        "title": "Reference Source",
        "publisher": "Example Publisher",
        "source_type": "academic",
        "active": active,
        "internal_note": "private",
    }


def _entity(
    *,
    published=False,
    review_status="draft",
    source_ids=None,
):
    return {
        "_id": ObjectId(),
        "entity_type": "community",
        "slug": "example-community",
        "name": "Example Community",
        "name_normalized": (
            "example community"
        ),
        "aliases": [],
        "summary": "Example summary.",
        "description": "Example description.",
        "regions": [
            "Assam",
        ],
        "languages": [
            "Assamese",
        ],
        "related_entities": [],
        "source_ids": (
            source_ids
            or []
        ),
        "review_status": review_status,
        "published": published,
        "provenance_notes": (
            "Editorial-only notes."
        ),
    }


def test_published_entity_requires_approval():
    with pytest.raises(
        CulturePublicationError
    ) as exc:
        create_culture_knowledge_entity(
            {
                "entity_type": "community",
                "slug": "example-community",
                "name": "Example Community",
                "published": True,
                "review_status": "draft",
            }
        )

    assert (
        exc.value.code
        == "CULTURE_NOT_APPROVED"
    )


def test_published_entity_requires_source():
    with pytest.raises(
        CulturePublicationError
    ) as exc:
        create_culture_knowledge_entity(
            {
                "entity_type": "community",
                "slug": "example-community",
                "name": "Example Community",
                "published": True,
                "review_status": "approved",
            }
        )

    assert (
        exc.value.code
        == "CULTURE_SOURCE_REQUIRED"
    )


def test_published_entity_rejects_inactive_source(
    monkeypatch,
):
    source_id = ObjectId()

    monkeypatch.setattr(
        culture_service,
        "get_culture_source_by_id",
        lambda value: _source(
            active=False
        ),
    )

    with pytest.raises(
        CulturePublicationError
    ) as exc:
        create_culture_knowledge_entity(
            {
                "entity_type": "community",
                "slug": "example-community",
                "name": "Example Community",
                "source_ids": [
                    str(source_id),
                ],
                "published": True,
                "review_status": "approved",
            }
        )

    assert (
        exc.value.code
        == "CULTURE_SOURCE_INACTIVE"
    )


def test_draft_entity_can_be_created_without_source(
    monkeypatch,
):
    created = _entity()

    monkeypatch.setattr(
        culture_service,
        "create_culture_entity",
        lambda **kwargs: {
            **deepcopy(created),
            **kwargs,
        },
    )

    result = (
        create_culture_knowledge_entity(
            {
                "entity_type": "community",
                "slug": "example-community",
                "name": "Example Community",
            }
        )
    )

    assert (
        result[
            "published"
        ]
        is False
    )

    assert (
        result[
            "review_status"
        ]
        == "draft"
    )


def test_duplicate_entity_becomes_service_conflict(
    monkeypatch,
):
    def duplicate(**kwargs):
        raise DuplicateKeyError(
            "duplicate"
        )

    monkeypatch.setattr(
        culture_service,
        "create_culture_entity",
        duplicate,
    )

    with pytest.raises(
        CultureConflictError
    ) as exc:
        create_culture_knowledge_entity(
            {
                "entity_type": "community",
                "slug": "example-community",
                "name": "Example Community",
            }
        )

    assert (
        exc.value.code
        == "CULTURE_ENTITY_EXISTS"
    )


def test_public_entity_hides_unpublished_record(
    monkeypatch,
):
    monkeypatch.setattr(
        culture_service,
        "get_culture_entity",
        lambda entity_type, slug: (
            _entity(
                published=False,
                review_status="approved",
            )
        ),
    )

    with pytest.raises(
        CultureNotFoundError
    ):
        get_public_culture_entity(
            "community",
            "example-community",
        )


def test_public_entity_serializer_exposes_only_safe_fields(
    monkeypatch,
):
    source = _source(
        active=True
    )

    entity = _entity(
        published=True,
        review_status="approved",
        source_ids=[
            source["_id"],
        ],
    )

    monkeypatch.setattr(
        culture_service,
        "get_culture_source_by_id",
        lambda value: source,
    )

    result = (
        serialize_culture_entity_public(
            entity
        )
    )

    assert (
        result[
            "name"
        ]
        == "Example Community"
    )

    assert (
        "review_status"
        not in result
    )

    assert (
        "source_ids"
        not in result
    )

    assert (
        "provenance_notes"
        not in result
    )

    assert len(
        result[
            "sources"
        ]
    ) == 1

    public_source = (
        result[
            "sources"
        ][0]
    )

    assert (
        "source_url_normalized"
        not in public_source
    )

    assert (
        "internal_note"
        not in public_source
    )


def test_public_list_forces_approved_published_filter(
    monkeypatch,
):
    captured = {}

    def fake_list(**kwargs):
        captured.update(
            kwargs
        )

        return []

    monkeypatch.setattr(
        culture_service,
        "list_culture_entities",
        fake_list,
    )

    result = (
        list_public_culture_entities(
            entity_type="community",
            region="Assam",
            language="Assamese",
            search="example",
            limit=20,
            skip=0,
        )
    )

    assert result == []

    assert (
        captured[
            "published"
        ]
        is True
    )

    assert (
        captured[
            "review_status"
        ]
        == "approved"
    )

    assert (
        captured[
            "entity_type"
        ]
        == "community"
    )


def test_festival_partial_timing_update_preserves_existing_values(
    monkeypatch,
):
    festival_id = ObjectId()

    existing = {
        "_id": festival_id,
        "slug": "example-festival",
        "name": "Example Festival",
        "name_normalized": (
            "example festival"
        ),
        "timing": {
            "date_type": "seasonal",
            "month": 4,
            "day": None,
            "date_text": "Usually in April.",
            "calendar_note": None,
        },
        "source_ids": [],
        "review_status": "draft",
        "published": False,
    }

    captured = {}

    monkeypatch.setattr(
        culture_service,
        "get_festival_by_id",
        lambda value: deepcopy(
            existing
        ),
    )

    def fake_update(
        festival_id_value,
        updates,
    ):
        captured[
            "festival_id"
        ] = festival_id_value

        captured[
            "updates"
        ] = deepcopy(
            updates
        )

        result = deepcopy(
            existing
        )

        result.update(
            updates
        )

        return result

    monkeypatch.setattr(
        culture_service,
        "update_festival",
        fake_update,
    )

    result = update_festival_knowledge(
        festival_id,
        {
            "timing": {
                "date_text": (
                    "Observed during spring."
                ),
            }
        },
    )

    assert (
        captured[
            "updates"
        ][
            "timing"
        ][
            "month"
        ]
        == 4
    )

    assert (
        captured[
            "updates"
        ][
            "timing"
        ][
            "date_type"
        ]
        == "seasonal"
    )

    assert (
        result[
            "timing"
        ][
            "date_text"
        ]
        == "Observed during spring."
    )


def test_missing_source_reference_is_rejected(
    monkeypatch,
):
    monkeypatch.setattr(
        culture_service,
        "get_culture_source_by_id",
        lambda value: None,
    )

    with pytest.raises(
        CulturePublicationError
    ) as exc:
        create_culture_knowledge_entity(
            {
                "entity_type": "history",
                "slug": "example-history",
                "name": "Example History",
                "source_ids": [
                    str(
                        ObjectId()
                    ),
                ],
            }
        )

    assert (
        exc.value.code
        == "CULTURE_SOURCE_NOT_FOUND"
    )
