"""Business operations for Soultie culture knowledge."""

from copy import deepcopy
from datetime import date, datetime

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.culture_repository import (
    create_culture_entity,
    create_festival,
    ensure_culture_source,
    get_culture_entity,
    get_culture_entity_by_id,
    get_culture_source_by_id,
    get_festival_by_id,
    get_festival_by_slug,
    list_culture_entities,
    list_festivals,
    update_culture_entity,
    update_festival,
)
from app.schemas.culture import (
    CULTURE_ENTITY_TYPES,
)
from app.services.culture_validation import (
    CultureValidationError,
    normalize_slug,
    validate_culture_entity_payload,
    validate_culture_source_payload,
    validate_festival_payload,
)


PUBLIC_SOURCE_FIELDS = (
    "_id",
    "source_url",
    "title",
    "publisher",
    "source_type",
    "published_at",
    "accessed_at",
)

PUBLIC_ENTITY_FIELDS = (
    "_id",
    "entity_type",
    "slug",
    "name",
    "aliases",
    "summary",
    "description",
    "regions",
    "languages",
    "related_entities",
)

PUBLIC_FESTIVAL_FIELDS = (
    "_id",
    "slug",
    "name",
    "aliases",
    "summary",
    "description",
    "regions",
    "communities",
    "tribes",
    "languages",
    "timing",
    "related_entities",
)


class CultureServiceError(RuntimeError):
    def __init__(
        self,
        code,
        message,
    ):
        self.code = code
        self.message = message

        super().__init__(
            message
        )


class CultureNotFoundError(
    CultureServiceError
):
    pass


class CultureConflictError(
    CultureServiceError
):
    pass


class CulturePublicationError(
    CultureServiceError
):
    pass


def _json_safe_value(
    value,
):
    if isinstance(
        value,
        ObjectId,
    ):
        return str(
            value
        )

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat()

    if isinstance(
        value,
        date,
    ):
        return value.isoformat()

    if isinstance(
        value,
        dict,
    ):
        return {
            key: _json_safe_value(
                item
            )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        list,
    ):
        return [
            _json_safe_value(
                item
            )
            for item in value
        ]

    return value


def _allowlisted_document(
    document,
    allowed_fields,
):
    if document is None:
        return None

    return {
        field: deepcopy(
            document[field]
        )
        for field in allowed_fields
        if field in document
    }


def serialize_culture_source_public(
    source,
):
    payload = _allowlisted_document(
        source,
        PUBLIC_SOURCE_FIELDS,
    )

    return _json_safe_value(
        payload
    )


def _resolve_source_documents(
    source_ids,
    *,
    require_active=False,
):
    source_ids = (
        source_ids
        or []
    )

    sources = []

    for source_id in source_ids:
        source = (
            get_culture_source_by_id(
                source_id
            )
        )

        if source is None:
            raise CulturePublicationError(
                "CULTURE_SOURCE_NOT_FOUND",
                (
                    "A referenced culture "
                    "source does not exist."
                ),
            )

        if (
            require_active
            and not source.get(
                "active",
                True,
            )
        ):
            raise CulturePublicationError(
                "CULTURE_SOURCE_INACTIVE",
                (
                    "Published culture content "
                    "cannot reference an inactive "
                    "source."
                ),
            )

        sources.append(
            source
        )

    return sources


def _public_sources_for_document(
    document,
):
    sources = []

    for source_id in (
        document.get(
            "source_ids"
        )
        or []
    ):
        source = (
            get_culture_source_by_id(
                source_id
            )
        )

        if source is None:
            continue

        if not source.get(
            "active",
            True,
        ):
            continue

        sources.append(
            serialize_culture_source_public(
                source
            )
        )

    return sources


def serialize_culture_entity_public(
    entity,
):
    payload = _allowlisted_document(
        entity,
        PUBLIC_ENTITY_FIELDS,
    )

    payload[
        "sources"
    ] = _public_sources_for_document(
        entity
    )

    return _json_safe_value(
        payload
    )


def serialize_festival_public(
    festival,
):
    payload = _allowlisted_document(
        festival,
        PUBLIC_FESTIVAL_FIELDS,
    )

    payload[
        "sources"
    ] = _public_sources_for_document(
        festival
    )

    return _json_safe_value(
        payload
    )


def _validate_source_references(
    source_ids,
):
    if source_ids is None:
        return []

    return _resolve_source_documents(
        source_ids,
        require_active=False,
    )


def _ensure_publishable(
    document,
):
    if not document.get(
        "published",
        False,
    ):
        return

    if (
        document.get(
            "review_status"
        )
        != "approved"
    ):
        raise CulturePublicationError(
            "CULTURE_NOT_APPROVED",
            (
                "Culture knowledge must be "
                "approved before publication."
            ),
        )

    source_ids = (
        document.get(
            "source_ids"
        )
        or []
    )

    if not source_ids:
        raise CulturePublicationError(
            "CULTURE_SOURCE_REQUIRED",
            (
                "Published culture knowledge "
                "must include at least one "
                "source."
            ),
        )

    _resolve_source_documents(
        source_ids,
        require_active=True,
    )


def _merge_document(
    existing,
    updates,
):
    candidate = deepcopy(
        existing
    )

    for key, value in (
        updates.items()
    ):
        candidate[
            key
        ] = deepcopy(
            value
        )

    return candidate


def register_culture_source(
    payload,
):
    validated = (
        validate_culture_source_payload(
            payload
        )
    )

    return ensure_culture_source(
        source_url=validated[
            "source_url"
        ],
        source_url_normalized=validated[
            "source_url_normalized"
        ],
        title=validated[
            "title"
        ],
        publisher=validated.get(
            "publisher"
        ),
        source_type=validated[
            "source_type"
        ],
        published_at=validated.get(
            "published_at"
        ),
        accessed_at=validated.get(
            "accessed_at"
        ),
    )


def create_culture_knowledge_entity(
    payload,
):
    validated = (
        validate_culture_entity_payload(
            payload,
            partial=False,
        )
    )

    source_ids = (
        validated.get(
            "source_ids"
        )
        or []
    )

    if source_ids:
        _validate_source_references(
            source_ids
        )

    prospective = {
        "review_status": validated.get(
            "review_status",
            "draft",
        ),
        "published": validated.get(
            "published",
            False,
        ),
        "source_ids": source_ids,
    }

    _ensure_publishable(
        prospective
    )

    try:
        entity = create_culture_entity(
            entity_type=validated[
                "entity_type"
            ],
            slug=validated[
                "slug"
            ],
            name=validated[
                "name"
            ],
            name_normalized=validated[
                "name_normalized"
            ],
        )

    except DuplicateKeyError as exc:
        raise CultureConflictError(
            "CULTURE_ENTITY_EXISTS",
            (
                "A culture entity with this "
                "type and slug already exists."
            ),
        ) from exc

    updates = {
        key: value
        for key, value
        in validated.items()
        if key not in {
            "entity_type",
            "slug",
            "name",
            "name_normalized",
        }
    }

    if updates:
        entity = update_culture_entity(
            entity["_id"],
            updates,
        )

    if entity is None:
        raise RuntimeError(
            "Culture entity could not be created."
        )

    return entity


def update_culture_knowledge_entity(
    entity_id,
    payload,
):
    existing = get_culture_entity_by_id(
        entity_id
    )

    if existing is None:
        raise CultureNotFoundError(
            "CULTURE_ENTITY_NOT_FOUND",
            "Culture entity was not found.",
        )

    validated = (
        validate_culture_entity_payload(
            payload,
            partial=True,
        )
    )

    if not validated:
        raise CultureValidationError(
            "entity",
            (
                "No culture entity update "
                "was provided."
            ),
        )

    if "source_ids" in validated:
        _validate_source_references(
            validated[
                "source_ids"
            ]
        )

    candidate = _merge_document(
        existing,
        validated,
    )

    _ensure_publishable(
        candidate
    )

    updated = update_culture_entity(
        existing["_id"],
        validated,
    )

    if updated is None:
        raise RuntimeError(
            "Culture entity could not be updated."
        )

    return updated


def create_festival_knowledge(
    payload,
):
    validated = validate_festival_payload(
        payload,
        partial=False,
    )

    source_ids = (
        validated.get(
            "source_ids"
        )
        or []
    )

    if source_ids:
        _validate_source_references(
            source_ids
        )

    prospective = {
        "review_status": validated.get(
            "review_status",
            "draft",
        ),
        "published": validated.get(
            "published",
            False,
        ),
        "source_ids": source_ids,
    }

    _ensure_publishable(
        prospective
    )

    try:
        festival = create_festival(
            slug=validated[
                "slug"
            ],
            name=validated[
                "name"
            ],
            name_normalized=validated[
                "name_normalized"
            ],
        )

    except DuplicateKeyError as exc:
        raise CultureConflictError(
            "FESTIVAL_EXISTS",
            (
                "A festival with this slug "
                "already exists."
            ),
        ) from exc

    updates = {
        key: value
        for key, value
        in validated.items()
        if key not in {
            "slug",
            "name",
            "name_normalized",
        }
    }

    if updates:
        festival = update_festival(
            festival["_id"],
            updates,
        )

    if festival is None:
        raise RuntimeError(
            "Festival could not be created."
        )

    return festival


def update_festival_knowledge(
    festival_id,
    payload,
):
    existing = get_festival_by_id(
        festival_id
    )

    if existing is None:
        raise CultureNotFoundError(
            "FESTIVAL_NOT_FOUND",
            "Festival was not found.",
        )

    validated = validate_festival_payload(
        payload,
        partial=True,
    )

    if not validated:
        raise CultureValidationError(
            "festival",
            "No festival update was provided.",
        )

    if "source_ids" in validated:
        _validate_source_references(
            validated[
                "source_ids"
            ]
        )

    if "timing" in validated:
        existing_timing = deepcopy(
            existing.get(
                "timing"
            )
            or {}
        )

        existing_timing.update(
            validated[
                "timing"
            ]
        )

        validated[
            "timing"
        ] = existing_timing

    candidate = _merge_document(
        existing,
        validated,
    )

    _ensure_publishable(
        candidate
    )

    updated = update_festival(
        existing["_id"],
        validated,
    )

    if updated is None:
        raise RuntimeError(
            "Festival could not be updated."
        )

    return updated


def get_public_culture_entity(
    entity_type,
    slug,
):
    if entity_type not in CULTURE_ENTITY_TYPES:
        raise CultureValidationError(
            "entity_type",
            "Unsupported culture entity type.",
        )

    normalized_slug = normalize_slug(
        slug
    )

    entity = get_culture_entity(
        entity_type,
        normalized_slug,
    )

    if (
        entity is None
        or not entity.get(
            "published",
            False,
        )
        or entity.get(
            "review_status"
        )
        != "approved"
    ):
        raise CultureNotFoundError(
            "CULTURE_ENTITY_NOT_FOUND",
            "Culture entity was not found.",
        )

    return serialize_culture_entity_public(
        entity
    )


def list_public_culture_entities(
    *,
    entity_type=None,
    region=None,
    language=None,
    search=None,
    limit=50,
    skip=0,
):
    if (
        entity_type is not None
        and entity_type
        not in CULTURE_ENTITY_TYPES
    ):
        raise CultureValidationError(
            "entity_type",
            "Unsupported culture entity type.",
        )

    entities = list_culture_entities(
        entity_type=entity_type,
        published=True,
        review_status="approved",
        region=region,
        language=language,
        search=search,
        limit=limit,
        skip=skip,
    )

    return [
        serialize_culture_entity_public(
            entity
        )
        for entity in entities
    ]


def get_public_festival(
    slug,
):
    normalized_slug = normalize_slug(
        slug
    )

    festival = get_festival_by_slug(
        normalized_slug
    )

    if (
        festival is None
        or not festival.get(
            "published",
            False,
        )
        or festival.get(
            "review_status"
        )
        != "approved"
    ):
        raise CultureNotFoundError(
            "FESTIVAL_NOT_FOUND",
            "Festival was not found.",
        )

    return serialize_festival_public(
        festival
    )


def list_public_festivals(
    *,
    region=None,
    community=None,
    tribe=None,
    language=None,
    search=None,
    limit=50,
    skip=0,
):
    festivals = list_festivals(
        published=True,
        review_status="approved",
        region=region,
        community=community,
        tribe=tribe,
        language=language,
        search=search,
        limit=limit,
        skip=skip,
    )

    return [
        serialize_festival_public(
            festival
        )
        for festival in festivals
    ]


__all__ = [
    "CultureConflictError",
    "CultureNotFoundError",
    "CulturePublicationError",
    "CultureServiceError",
    "create_culture_knowledge_entity",
    "create_festival_knowledge",
    "get_public_culture_entity",
    "get_public_festival",
    "list_public_culture_entities",
    "list_public_festivals",
    "register_culture_source",
    "serialize_culture_entity_public",
    "serialize_culture_source_public",
    "serialize_festival_public",
    "update_culture_knowledge_entity",
    "update_festival_knowledge",
]
