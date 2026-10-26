"""Authenticated Soultie Notification REST API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.notification_service import (
    NotificationServiceError,
    NotificationValidationError,
    get_my_notification_preferences,
    get_my_unread_notification_count,
    list_my_notifications,
    mark_all_my_notifications_read,
    mark_my_notification_read,
    update_my_notification_preferences,
)


notifications_bp = Blueprint(
    "notifications_v1",
    __name__,
    url_prefix="/notifications",
)


def _service_error(
    error,
):
    return jsonify(
        {
            "error": {
                "code": error.code,
                "message": error.message,
            }
        }
    ), error.status_code


def _parse_unread_only():
    raw_value = request.args.get(
        "unread_only"
    )

    if raw_value is None:
        return False

    if not isinstance(
        raw_value,
        str,
    ):
        raise NotificationValidationError(
            "INVALID_NOTIFICATION_LIST_QUERY",
            (
                "unread_only must be "
                "true or false."
            ),
            status_code=400,
        )

    value = (
        raw_value
        .strip()
        .lower()
    )

    if value == "true":
        return True

    if value == "false":
        return False

    raise NotificationValidationError(
        "INVALID_NOTIFICATION_LIST_QUERY",
        (
            "unread_only must be "
            "true or false."
        ),
        status_code=400,
    )


@notifications_bp.get("")
@auth_required
def list_notifications():
    try:
        result = (
            list_my_notifications(
                g.current_user[
                    "_id"
                ],
                limit=(
                    request.args.get(
                        "limit",
                        "20",
                    )
                ),
                cursor=(
                    request.args.get(
                        "cursor"
                    )
                ),
                unread_only=(
                    _parse_unread_only()
                ),
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@notifications_bp.get(
    "/preferences"
)
@auth_required
def get_notification_preferences():
    try:
        result = (
            get_my_notification_preferences(
                g.current_user[
                    "_id"
                ]
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@notifications_bp.put(
    "/preferences"
)
@auth_required
def update_notification_preferences():
    try:
        result = (
            update_my_notification_preferences(
                g.current_user[
                    "_id"
                ],
                request.get_json(
                    silent=True
                ),
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@notifications_bp.get(
    "/unread-count"
)
@auth_required
def get_unread_notification_count():
    try:
        result = (
            get_my_unread_notification_count(
                g.current_user[
                    "_id"
                ]
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@notifications_bp.post(
    "/<notification_id>/read"
)
@auth_required
def mark_notification_read(
    notification_id,
):
    try:
        result = (
            mark_my_notification_read(
                user_id=(
                    g.current_user[
                        "_id"
                    ]
                ),
                notification_id=(
                    notification_id
                ),
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@notifications_bp.post(
    "/read-all"
)
@auth_required
def mark_all_notifications_read():
    try:
        result = (
            mark_all_my_notifications_read(
                g.current_user[
                    "_id"
                ]
            )
        )

    except NotificationServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


__all__ = [
    "notifications_bp",
]
