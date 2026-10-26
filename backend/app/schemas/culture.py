"""Canonical culture knowledge data models for Soultie.

This module defines server-side document templates only. Cultural identity is
always user-declared; these catalog records must never be used to infer a
user's ethnicity, tribe, community, religion, or other sensitive attributes.
"""

from copy import deepcopy
from datetime import datetime, timezone


CULTURE_ENTITY_TYPES = {
    "community",
    "tribe",
    "language",
    "heritage",
    "tradition",
    "history",
}

CULTURE_REVIEW_STATUSES = {
    "draft",
    "in_review",
    "approved",
    "rejected",
}

CULTURE_SOURCE_TYPES = {
    "government",
    "academic",
    "museum_archive",
    "cultural_institution",
    "official_community_source",
    "reputable_reference",
    "other",
}

FESTIVAL_DATE_TYPES = {
    "fixed",
    "lunar",
    "seasonal",
    "variable",
    "unknown",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_culture_source_document(
    *,
    source_url,
    source_url_normalized,
    title,
    publisher=None,
    source_type="other",
    published_at=None,
    accessed_at=None,
    now=None,
):
    """Build a provenance record for a culture knowledge source."""

    timestamp = now or utc_now()

    return {
        "source_url": source_url,
        "source_url_normalized": source_url_normalized,
        "title": title,
        "publisher": publisher,
        "source_type": source_type,
        "published_at": published_at,
        "accessed_at": accessed_at or timestamp,
        "active": True,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_culture_entity_document(
    *,
    entity_type,
    slug,
    name,
    name_normalized,
    now=None,
):
    """Build a canonical culture entity document.

    Entity content is editorial/catalog data. It is intentionally separated
    from user profile culture selections.
    """

    timestamp = now or utc_now()

    return {
        "entity_type": entity_type,
        "slug": slug,
        "name": name,
        "name_normalized": name_normalized,
        "aliases": [],
        "search_terms": [],
        "summary": None,
        "description": None,
        "regions": [],
        "languages": [],
        "related_entities": [],
        "source_ids": [],
        "provenance_notes": None,
        "review_status": "draft",
        "published": False,
        "reviewed_at": None,
        "reviewed_by": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def build_festival_document(
    *,
    slug,
    name,
    name_normalized,
    now=None,
):
    """Build a canonical festival knowledge document."""

    timestamp = now or utc_now()

    return {
        "slug": slug,
        "name": name,
        "name_normalized": name_normalized,
        "aliases": [],
        "search_terms": [],
        "summary": None,
        "description": None,
        "regions": [],
        "communities": [],
        "tribes": [],
        "languages": [],
        "timing": {
            "date_type": "unknown",
            "month": None,
            "day": None,
            "date_text": None,
            "calendar_note": None,
        },
        "related_entities": [],
        "source_ids": [],
        "provenance_notes": None,
        "review_status": "draft",
        "published": False,
        "reviewed_at": None,
        "reviewed_by": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def clone_culture_source_template(
    *,
    source_url,
    source_url_normalized,
    title,
):
    return deepcopy(
        build_culture_source_document(
            source_url=source_url,
            source_url_normalized=source_url_normalized,
            title=title,
        )
    )


def clone_culture_entity_template(
    *,
    entity_type,
    slug,
    name,
    name_normalized,
):
    return deepcopy(
        build_culture_entity_document(
            entity_type=entity_type,
            slug=slug,
            name=name,
            name_normalized=name_normalized,
        )
    )


def clone_festival_template(
    *,
    slug,
    name,
    name_normalized,
):
    return deepcopy(
        build_festival_document(
            slug=slug,
            name=name,
            name_normalized=name_normalized,
        )
    )


__all__ = [
    "CULTURE_ENTITY_TYPES",
    "CULTURE_REVIEW_STATUSES",
    "CULTURE_SOURCE_TYPES",
    "FESTIVAL_DATE_TYPES",
    "build_culture_entity_document",
    "build_culture_source_document",
    "build_festival_document",
    "clone_culture_entity_template",
    "clone_culture_source_template",
    "clone_festival_template",
    "utc_now",
]
