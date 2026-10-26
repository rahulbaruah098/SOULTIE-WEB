"""Public Soultie culture knowledge API."""

from flask import (
    Blueprint,
    jsonify,
    request,
)

from app.services.culture_service import (
    CultureNotFoundError,
    get_public_culture_entity,
    get_public_festival,
    list_public_culture_entities,
    list_public_festivals,
)
from app.services.culture_validation import (
    CultureValidationError,
)


culture_bp = Blueprint(
    "culture_v1",
    __name__,
    url_prefix="/culture",
)


DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 200
MAX_FILTER_LENGTH = 120


def _error_response(
    code,
    message,
    status,
    *,
    field=None,
):
    payload = {
        "error": {
            "code": code,
            "message": message,
        }
    }

    if field:
        payload[
            "error"
        ][
            "field"
        ] = field

    return jsonify(
        payload
    ), status


def _query_text(
    name,
    *,
    max_length,
):
    value = request.args.get(
        name
    )

    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise CultureValidationError(
            name,
            (
                f"Must be {max_length} "
                "characters or fewer."
            ),
        )

    return value


def _pagination():
    raw_limit = request.args.get(
        "limit"
    )

    raw_offset = request.args.get(
        "offset"
    )

    try:
        limit = (
            DEFAULT_PAGE_SIZE
            if raw_limit is None
            else int(
                raw_limit
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        raise CultureValidationError(
            "limit",
            "Limit must be an integer.",
        )

    try:
        offset = (
            0
            if raw_offset is None
            else int(
                raw_offset
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        raise CultureValidationError(
            "offset",
            "Offset must be an integer.",
        )

    if (
        limit < 1
        or limit > MAX_PAGE_SIZE
    ):
        raise CultureValidationError(
            "limit",
            (
                f"Limit must be between "
                f"1 and {MAX_PAGE_SIZE}."
            ),
        )

    if offset < 0:
        raise CultureValidationError(
            "offset",
            "Offset cannot be negative.",
        )

    return limit, offset


def _validation_error(
    error,
):
    return _error_response(
        "CULTURE_VALIDATION_ERROR",
        error.message,
        400,
        field=error.field,
    )


def _not_found_error(
    error,
):
    return _error_response(
        error.code,
        error.message,
        404,
    )


@culture_bp.get("/entities")
def culture_entities():
    try:
        limit, offset = (
            _pagination()
        )

        entity_type = _query_text(
            "type",
            max_length=50,
        )

        region = _query_text(
            "region",
            max_length=MAX_FILTER_LENGTH,
        )

        language = _query_text(
            "language",
            max_length=MAX_FILTER_LENGTH,
        )

        search = _query_text(
            "q",
            max_length=MAX_SEARCH_LENGTH,
        )

        entities = (
            list_public_culture_entities(
                entity_type=entity_type,
                region=region,
                language=language,
                search=search,
                limit=limit,
                skip=offset,
            )
        )

    except CultureValidationError as exc:
        return _validation_error(
            exc
        )

    return jsonify(
        {
            "entities": entities,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(
                    entities
                ),
            },
        }
    )


@culture_bp.get(
    "/entities/<entity_type>/<slug>"
)
def culture_entity_detail(
    entity_type,
    slug,
):
    try:
        entity = (
            get_public_culture_entity(
                entity_type,
                slug,
            )
        )

    except CultureValidationError as exc:
        return _validation_error(
            exc
        )

    except CultureNotFoundError as exc:
        return _not_found_error(
            exc
        )

    return jsonify(
        {
            "entity": entity,
        }
    )


@culture_bp.get("/festivals")
def culture_festivals():
    try:
        limit, offset = (
            _pagination()
        )

        region = _query_text(
            "region",
            max_length=MAX_FILTER_LENGTH,
        )

        community = _query_text(
            "community",
            max_length=MAX_FILTER_LENGTH,
        )

        tribe = _query_text(
            "tribe",
            max_length=MAX_FILTER_LENGTH,
        )

        language = _query_text(
            "language",
            max_length=MAX_FILTER_LENGTH,
        )

        search = _query_text(
            "q",
            max_length=MAX_SEARCH_LENGTH,
        )

        festivals = (
            list_public_festivals(
                region=region,
                community=community,
                tribe=tribe,
                language=language,
                search=search,
                limit=limit,
                skip=offset,
            )
        )

    except CultureValidationError as exc:
        return _validation_error(
            exc
        )

    return jsonify(
        {
            "festivals": festivals,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": len(
                    festivals
                ),
            },
        }
    )


@culture_bp.get(
    "/festivals/<slug>"
)
def culture_festival_detail(
    slug,
):
    try:
        festival = (
            get_public_festival(
                slug
            )
        )

    except CultureValidationError as exc:
        return _validation_error(
            exc
        )

    except CultureNotFoundError as exc:
        return _not_found_error(
            exc
        )

    return jsonify(
        {
            "festival": festival,
        }
    )


__all__ = [
    "culture_bp",
]
