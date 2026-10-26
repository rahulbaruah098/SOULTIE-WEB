"""Authenticated Soultie profile media API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
    send_file,
)


from app.schemas.media import (
    MEDIA_KIND_PROFILE_VIDEO,
)
from app.security.authentication import (
    auth_required,
)
from app.services.media_service import (
    MediaServiceError,
    delete_profile_media,
    get_viewable_profile_media,
    list_profile_media,
    reorder_profile_media,
    set_profile_primary_media,
    upload_profile_media,
)
from app.services.media_storage_service import (
    MediaStorageError,
    get_media_storage,
)


media_bp = Blueprint(
    "media_v1",
    __name__,
    url_prefix="/media",
)


def _error_response(
    code,
    message,
    status_code,
):
    return jsonify(
        {
            "error": {
                "code": code,
                "message": message,
            }
        }
    ), status_code


def _service_error(
    error,
):
    return _error_response(
        error.code,
        error.message,
        error.status_code,
    )


def _viewable_media_or_error(
    media_id,
):
    try:
        media = get_viewable_profile_media(
            g.current_user["_id"],
            media_id,
        )

    except MediaServiceError as exc:
        return (
            None,
            _service_error(
                exc
            ),
        )

    return (
        media,
        None,
    )


def _serve_storage_object(
    *,
    storage_key,
    mime_type,
):
    try:
        storage = get_media_storage()

        path = storage.get_internal_path(
            storage_key
        )

    except MediaStorageError:
        return _error_response(
            "MEDIA_CONTENT_NOT_FOUND",
            "The requested media content is unavailable.",
            404,
        )

    response = send_file(
        path,
        mimetype=mime_type,
        as_attachment=False,
        conditional=True,
    )

    # Media is accessible only through authenticated API routes.
    # Private caching is allowed without making the underlying
    # storage location public.
    response.headers[
        "Cache-Control"
    ] = "private, max-age=3600"

    response.headers[
        "X-Content-Type-Options"
    ] = "nosniff"

    return response


@media_bp.post("")
@auth_required
def upload_media():
    uploaded_file = request.files.get(
        "file"
    )

    if uploaded_file is None:
        return _error_response(
            "MEDIA_FILE_REQUIRED",
            (
                "A profile media file is "
                "required."
            ),
            400,
        )

    kind = request.form.get(
        "kind"
    )

    try:
        content = uploaded_file.read()

        result = upload_profile_media(
            g.current_user["_id"],
            kind=kind,
            content=content,
        )

    except MediaServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    ), 201


@media_bp.get("")
@auth_required
def list_media():
    try:
        result = list_profile_media(
            g.current_user["_id"]
        )

    except MediaServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    ), 200


@media_bp.patch(
    "/reorder"
)
@auth_required
def reorder_media():
    body = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        result = reorder_profile_media(
            g.current_user["_id"],
            body.get(
                "media_ids"
            ),
        )

    except MediaServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    ), 200


@media_bp.patch(
    "/<media_id>/primary"
)
@auth_required
def set_primary_media(
    media_id,
):
    try:
        result = (
            set_profile_primary_media(
                g.current_user["_id"],
                media_id,
            )
        )

    except MediaServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    ), 200


@media_bp.delete(
    "/<media_id>"
)
@auth_required
def delete_media(
    media_id,
):
    try:
        result = delete_profile_media(
            g.current_user["_id"],
            media_id,
        )

    except MediaServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    ), 200

@media_bp.get(
    "/<media_id>/content"
)
@auth_required
def media_content(
    media_id,
):
    media, error = (
        _viewable_media_or_error(
            media_id
        )
    )

    if error is not None:
        return error

    return _serve_storage_object(
        storage_key=media[
            "storage_key"
        ],
        mime_type=media[
            "mime_type"
        ],
    )


@media_bp.get(
    "/<media_id>/thumbnail"
)
@auth_required
def media_thumbnail(
    media_id,
):
    media, error = (
        _viewable_media_or_error(
            media_id
        )
    )

    if error is not None:
        return error

    if (
        media.get(
            "kind"
        )
        != MEDIA_KIND_PROFILE_VIDEO
        or not media.get(
            "thumbnail_storage_key"
        )
    ):
        return _error_response(
            "MEDIA_THUMBNAIL_NOT_FOUND",
            (
                "The requested media item "
                "does not have a thumbnail."
            ),
            404,
        )

    return _serve_storage_object(
        storage_key=media[
            "thumbnail_storage_key"
        ],
        mime_type=media[
            "thumbnail_mime_type"
        ],
    )


__all__ = [
    "media_bp",
]
