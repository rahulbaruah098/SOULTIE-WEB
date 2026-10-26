"""Tests for Soultie culture validation and normalization."""

import pytest

from app.services.culture_validation import (
    CultureValidationError,
    normalize_search_text,
    normalize_slug,
    normalize_source_url,
    validate_culture_entity_payload,
    validate_culture_source_payload,
    validate_festival_payload,
)


def test_normalize_search_text():
    assert (
        normalize_search_text(
            "  Bihu   Festival  "
        )
        == "bihu festival"
    )


def test_normalize_slug():
    assert (
        normalize_slug(
            "  Rongali_Bihu Festival  "
        )
        == "rongali-bihu-festival"
    )


def test_normalize_source_url_removes_fragment_and_default_port():
    assert (
        normalize_source_url(
            "HTTPS://Example.COM:443/culture?id=1#section"
        )
        == "https://example.com/culture?id=1"
    )


def test_normalize_source_url_rejects_unsupported_scheme():
    with pytest.raises(
        CultureValidationError
    ) as exc:
        normalize_source_url(
            "ftp://example.com/file"
        )

    assert (
        exc.value.field
        == "source_url"
    )


def test_validate_culture_source_payload():
    result = (
        validate_culture_source_payload(
            {
                "source_url": (
                    "https://example.com/reference"
                ),
                "title": (
                    "Cultural Reference"
                ),
                "publisher": (
                    "Example Institution"
                ),
                "source_type": (
                    "cultural_institution"
                ),
            }
        )
    )

    assert (
        result[
            "source_url_normalized"
        ]
        == "https://example.com/reference"
    )

    assert (
        result[
            "source_type"
        ]
        == "cultural_institution"
    )


def test_validate_culture_entity_payload_normalizes_core_fields():
    result = (
        validate_culture_entity_payload(
            {
                "entity_type": "community",
                "slug": "Assamese Community",
                "name": "Assamese",
                "aliases": [
                    "Asomiya",
                    "asomiya",
                ],
                "regions": [
                    "Assam",
                ],
                "languages": [
                    "Assamese",
                ],
            }
        )
    )

    assert (
        result[
            "entity_type"
        ]
        == "community"
    )

    assert (
        result[
            "slug"
        ]
        == "assamese-community"
    )

    assert (
        result[
            "name_normalized"
        ]
        == "assamese"
    )

    assert result[
        "aliases"
    ] == [
        "Asomiya",
    ]


def test_validate_culture_entity_rejects_unknown_type():
    with pytest.raises(
        CultureValidationError
    ) as exc:
        validate_culture_entity_payload(
            {
                "entity_type": (
                    "unsupported"
                ),
                "slug": "example",
                "name": "Example",
            }
        )

    assert (
        exc.value.field
        == "entity_type"
    )


def test_validate_culture_entity_rejects_unknown_field():
    with pytest.raises(
        CultureValidationError
    ):
        validate_culture_entity_payload(
            {
                "entity_type": (
                    "tradition"
                ),
                "slug": "example",
                "name": "Example",
                "unexpected": True,
            }
        )


def test_validate_partial_culture_entity():
    result = (
        validate_culture_entity_payload(
            {
                "summary": (
                    "Updated summary."
                ),
                "regions": [
                    "Assam",
                    "Assam",
                ],
            },
            partial=True,
        )
    )

    assert (
        result[
            "summary"
        ]
        == "Updated summary."
    )

    assert result[
        "regions"
    ] == [
        "Assam",
    ]


def test_validate_festival_payload():
    result = (
        validate_festival_payload(
            {
                "slug": "rongali-bihu",
                "name": "Rongali Bihu",
                "regions": [
                    "Assam",
                ],
                "languages": [
                    "Assamese",
                ],
                "timing": {
                    "date_type": (
                        "seasonal"
                    ),
                    "month": 4,
                    "date_text": (
                        "Usually observed "
                        "in April."
                    ),
                },
            }
        )
    )

    assert (
        result[
            "slug"
        ]
        == "rongali-bihu"
    )

    assert (
        result[
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
            "month"
        ]
        == 4
    )


def test_validate_festival_rejects_invalid_month():
    with pytest.raises(
        CultureValidationError
    ) as exc:
        validate_festival_payload(
            {
                "slug": "example",
                "name": "Example",
                "timing": {
                    "month": 13,
                },
            }
        )

    assert (
        exc.value.field
        == "timing.month"
    )


def test_published_must_be_boolean():
    with pytest.raises(
        CultureValidationError
    ) as exc:
        validate_culture_entity_payload(
            {
                "entity_type": (
                    "history"
                ),
                "slug": "example-history",
                "name": "Example History",
                "published": "yes",
            }
        )

    assert (
        exc.value.field
        == "published"
    )
