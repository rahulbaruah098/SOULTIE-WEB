"""MongoDB repository operations for Soultie culture knowledge."""

import re
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.schemas.culture import (
    build_culture_entity_document,
    build_culture_source_document,
    build_festival_document,
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def _to_object_id(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return value

    try:
        return ObjectId(
            str(value)
        )

    except Exception:
        return None


def _safe_regex(
    value,
):
    if not isinstance(
        value,
        str,
    ):
        return None

    value = value.strip()

    if not value:
        return None

    return re.compile(
        re.escape(
            value
        ),
        re.IGNORECASE,
    )


def _validate_update_paths(
    updates,
    *,
    protected=None,
):
    protected_fields = {
        "_id",
        "created_at",
    }

    protected_fields.update(
        protected
        or set()
    )

    for path in updates:
        root = path.split(
            ".",
            1,
        )[0]

        if (
            root in protected_fields
            or path.startswith(
                "$"
            )
            or ".$" in path
        ):
            raise ValueError(
                "Protected culture field update."
            )


def _apply_update(
    collection,
    document_id,
    updates,
    *,
    protected=None,
):
    object_id = _to_object_id(
        document_id
    )

    if object_id is None:
        return None

    if not updates:
        return collection.find_one(
            {
                "_id": object_id,
            }
        )

    _validate_update_paths(
        updates,
        protected=protected,
    )

    fields = dict(
        updates
    )

    fields[
        "updated_at"
    ] = utc_now()

    result = collection.update_one(
        {
            "_id": object_id,
        },
        {
            "$set": fields,
        },
    )

    if result.matched_count != 1:
        return None

    return collection.find_one(
        {
            "_id": object_id,
        }
    )


def create_culture_source(
    *,
    source_url,
    source_url_normalized,
    title,
    publisher=None,
    source_type="other",
    published_at=None,
    accessed_at=None,
):
    document = (
        build_culture_source_document(
            source_url=source_url,
            source_url_normalized=(
                source_url_normalized
            ),
            title=title,
            publisher=publisher,
            source_type=source_type,
            published_at=published_at,
            accessed_at=accessed_at,
        )
    )

    result = (
        mongo.db.culture_sources.insert_one(
            document
        )
    )

    return mongo.db.culture_sources.find_one(
        {
            "_id": result.inserted_id,
        }
    )


def get_culture_source_by_id(
    source_id,
):
    object_id = _to_object_id(
        source_id
    )

    if object_id is None:
        return None

    return mongo.db.culture_sources.find_one(
        {
            "_id": object_id,
        }
    )


def get_culture_source_by_url(
    source_url_normalized,
):
    if not source_url_normalized:
        return None

    return mongo.db.culture_sources.find_one(
        {
            "source_url_normalized": (
                source_url_normalized
            ),
        }
    )


def ensure_culture_source(
    *,
    source_url,
    source_url_normalized,
    title,
    publisher=None,
    source_type="other",
    published_at=None,
    accessed_at=None,
):
    existing = get_culture_source_by_url(
        source_url_normalized
    )

    if existing is not None:
        return existing

    try:
        return create_culture_source(
            source_url=source_url,
            source_url_normalized=(
                source_url_normalized
            ),
            title=title,
            publisher=publisher,
            source_type=source_type,
            published_at=published_at,
            accessed_at=accessed_at,
        )

    except DuplicateKeyError:
        return get_culture_source_by_url(
            source_url_normalized
        )


def update_culture_source(
    source_id,
    updates,
):
    return _apply_update(
        mongo.db.culture_sources,
        source_id,
        updates,
        protected={
            "source_url_normalized",
        },
    )


def list_culture_sources(
    *,
    active=None,
    limit=50,
    skip=0,
):
    query = {}

    if active is not None:
        query[
            "active"
        ] = bool(
            active
        )

    return list(
        mongo.db.culture_sources.find(
            query
        )
        .sort(
            "created_at",
            DESCENDING,
        )
        .skip(
            max(
                0,
                int(skip),
            )
        )
        .limit(
            max(
                1,
                min(
                    int(limit),
                    100,
                ),
            )
        )
    )


def create_culture_entity(
    *,
    entity_type,
    slug,
    name,
    name_normalized,
):
    document = (
        build_culture_entity_document(
            entity_type=entity_type,
            slug=slug,
            name=name,
            name_normalized=name_normalized,
        )
    )

    result = (
        mongo.db.culture_entities.insert_one(
            document
        )
    )

    return mongo.db.culture_entities.find_one(
        {
            "_id": result.inserted_id,
        }
    )


def get_culture_entity_by_id(
    entity_id,
):
    object_id = _to_object_id(
        entity_id
    )

    if object_id is None:
        return None

    return mongo.db.culture_entities.find_one(
        {
            "_id": object_id,
        }
    )


def get_culture_entity(
    entity_type,
    slug,
):
    if (
        not entity_type
        or not slug
    ):
        return None

    return mongo.db.culture_entities.find_one(
        {
            "entity_type": entity_type,
            "slug": slug,
        }
    )


def ensure_culture_entity(
    *,
    entity_type,
    slug,
    name,
    name_normalized,
):
    existing = get_culture_entity(
        entity_type,
        slug,
    )

    if existing is not None:
        return existing

    try:
        return create_culture_entity(
            entity_type=entity_type,
            slug=slug,
            name=name,
            name_normalized=(
                name_normalized
            ),
        )

    except DuplicateKeyError:
        return get_culture_entity(
            entity_type,
            slug,
        )


def update_culture_entity(
    entity_id,
    updates,
):
    return _apply_update(
        mongo.db.culture_entities,
        entity_id,
        updates,
        protected={
            "entity_type",
            "slug",
        },
    )


def list_culture_entities(
    *,
    entity_type=None,
    published=None,
    review_status=None,
    region=None,
    language=None,
    search=None,
    limit=50,
    skip=0,
):
    query = {}

    if entity_type:
        query[
            "entity_type"
        ] = entity_type

    if published is not None:
        query[
            "published"
        ] = bool(
            published
        )

    if review_status:
        query[
            "review_status"
        ] = review_status

    if region:
        query[
            "regions"
        ] = region

    if language:
        query[
            "languages"
        ] = language

    search_regex = _safe_regex(
        search
    )

    if search_regex is not None:
        query[
            "$or"
        ] = [
            {
                "name": search_regex,
            },
            {
                "aliases": search_regex,
            },
            {
                "search_terms": search_regex,
            },
        ]

    return list(
        mongo.db.culture_entities.find(
            query
        )
        .sort(
            [
                (
                    "name_normalized",
                    ASCENDING,
                ),
                (
                    "_id",
                    ASCENDING,
                ),
            ]
        )
        .skip(
            max(
                0,
                int(skip),
            )
        )
        .limit(
            max(
                1,
                min(
                    int(limit),
                    100,
                ),
            )
        )
    )


def count_culture_entities(
    *,
    entity_type=None,
    published=None,
    review_status=None,
):
    query = {}

    if entity_type:
        query[
            "entity_type"
        ] = entity_type

    if published is not None:
        query[
            "published"
        ] = bool(
            published
        )

    if review_status:
        query[
            "review_status"
        ] = review_status

    return (
        mongo.db.culture_entities.count_documents(
            query
        )
    )


def create_festival(
    *,
    slug,
    name,
    name_normalized,
):
    document = (
        build_festival_document(
            slug=slug,
            name=name,
            name_normalized=name_normalized,
        )
    )

    result = (
        mongo.db.festivals.insert_one(
            document
        )
    )

    return mongo.db.festivals.find_one(
        {
            "_id": result.inserted_id,
        }
    )


def get_festival_by_id(
    festival_id,
):
    object_id = _to_object_id(
        festival_id
    )

    if object_id is None:
        return None

    return mongo.db.festivals.find_one(
        {
            "_id": object_id,
        }
    )


def get_festival_by_slug(
    slug,
):
    if not slug:
        return None

    return mongo.db.festivals.find_one(
        {
            "slug": slug,
        }
    )


def ensure_festival(
    *,
    slug,
    name,
    name_normalized,
):
    existing = get_festival_by_slug(
        slug
    )

    if existing is not None:
        return existing

    try:
        return create_festival(
            slug=slug,
            name=name,
            name_normalized=(
                name_normalized
            ),
        )

    except DuplicateKeyError:
        return get_festival_by_slug(
            slug
        )


def update_festival(
    festival_id,
    updates,
):
    return _apply_update(
        mongo.db.festivals,
        festival_id,
        updates,
        protected={
            "slug",
        },
    )


def list_festivals(
    *,
    published=None,
    review_status=None,
    region=None,
    community=None,
    tribe=None,
    language=None,
    search=None,
    limit=50,
    skip=0,
):
    query = {}

    if published is not None:
        query[
            "published"
        ] = bool(
            published
        )

    if review_status:
        query[
            "review_status"
        ] = review_status

    if region:
        query[
            "regions"
        ] = region

    if community:
        query[
            "communities"
        ] = community

    if tribe:
        query[
            "tribes"
        ] = tribe

    if language:
        query[
            "languages"
        ] = language

    search_regex = _safe_regex(
        search
    )

    if search_regex is not None:
        query[
            "$or"
        ] = [
            {
                "name": search_regex,
            },
            {
                "aliases": search_regex,
            },
            {
                "search_terms": search_regex,
            },
        ]

    return list(
        mongo.db.festivals.find(
            query
        )
        .sort(
            [
                (
                    "name_normalized",
                    ASCENDING,
                ),
                (
                    "_id",
                    ASCENDING,
                ),
            ]
        )
        .skip(
            max(
                0,
                int(skip),
            )
        )
        .limit(
            max(
                1,
                min(
                    int(limit),
                    100,
                ),
            )
        )
    )


def count_festivals(
    *,
    published=None,
    review_status=None,
):
    query = {}

    if published is not None:
        query[
            "published"
        ] = bool(
            published
        )

    if review_status:
        query[
            "review_status"
        ] = review_status

    return mongo.db.festivals.count_documents(
        query
    )


__all__ = [
    "count_culture_entities",
    "count_festivals",
    "create_culture_entity",
    "create_culture_source",
    "create_festival",
    "ensure_culture_entity",
    "ensure_culture_source",
    "ensure_festival",
    "get_culture_entity",
    "get_culture_entity_by_id",
    "get_culture_source_by_id",
    "get_culture_source_by_url",
    "get_festival_by_id",
    "get_festival_by_slug",
    "list_culture_entities",
    "list_culture_sources",
    "list_festivals",
    "update_culture_entity",
    "update_culture_source",
    "update_festival",
]
