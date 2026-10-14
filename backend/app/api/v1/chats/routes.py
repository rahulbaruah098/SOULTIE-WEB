"""Authenticated Xuoroni chat REST API."""

from flask import (
    Blueprint,
    g,
    jsonify,
    request,
)

from app.security.authentication import (
    auth_required,
)
from app.services.chat_service import (
    ChatServiceError,
    ensure_chat_for_match,
    get_my_conversation,
    list_my_conversations,
    list_my_messages,
    mark_chat_delivered,
    mark_chat_read,
    send_chat_message,
)


chats_bp = Blueprint(
    "chats_v1",
    __name__,
    url_prefix="/chats",
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


def _validation_error(
    message,
):
    return jsonify(
        {
            "error": {
                "code": (
                    "CHAT_VALIDATION_ERROR"
                ),
                "message": message,
            }
        }
    ), 400


def _required_json_body():
    body = request.get_json(
        silent=True
    )

    if not isinstance(
        body,
        dict,
    ):
        return (
            None,
            _validation_error(
                (
                    "A JSON request body "
                    "is required."
                )
            ),
        )

    return (
        body,
        None,
    )


def _optional_json_body():
    raw_body = request.get_data(
        cache=True
    )

    if not raw_body:
        return (
            {},
            None,
        )

    body = request.get_json(
        silent=True
    )

    if not isinstance(
        body,
        dict,
    ):
        return (
            None,
            _validation_error(
                (
                    "Request body must "
                    "be a JSON object."
                )
            ),
        )

    return (
        body,
        None,
    )


@chats_bp.post(
    "/matches/<match_id>"
)
@auth_required
def create_or_get_chat(
    match_id,
):
    try:
        result = ensure_chat_for_match(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            match_id=match_id,
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    status_code = (
        201
        if result.get(
            "created"
        )
        else 200
    )

    return jsonify(
        result
    ), status_code


@chats_bp.get("")
@auth_required
def list_chats():
    try:
        result = list_my_conversations(
            g.current_user[
                "_id"
            ],
            status=request.args.get(
                "status",
                "active",
            ),
            limit=request.args.get(
                "limit",
                "50",
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.get(
    "/<conversation_id>"
)
@auth_required
def get_chat(
    conversation_id,
):
    try:
        result = get_my_conversation(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.get(
    "/<conversation_id>/messages"
)
@auth_required
def get_chat_messages(
    conversation_id,
):
    try:
        result = list_my_messages(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            before_sequence=(
                request.args.get(
                    "before_sequence"
                )
            ),
            limit=request.args.get(
                "limit"
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.post(
    "/<conversation_id>/messages"
)
@auth_required
def create_chat_message(
    conversation_id,
):
    body, error_response = (
        _required_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = send_chat_message(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            client_message_id=(
                body.get(
                    "client_message_id"
                )
            ),
            body=body.get(
                "body"
            ),
            message_type=body.get(
                "message_type",
                "text",
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    status_code = (
        201
        if result.get(
            "created"
        )
        else 200
    )

    return jsonify(
        result
    ), status_code


@chats_bp.post(
    "/<conversation_id>/delivered"
)
@auth_required
def mark_chat_messages_delivered(
    conversation_id,
):
    body, error_response = (
        _optional_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = mark_chat_delivered(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            through_sequence=(
                body.get(
                    "through_sequence"
                )
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


@chats_bp.post(
    "/<conversation_id>/read"
)
@auth_required
def mark_chat_messages_read(
    conversation_id,
):
    body, error_response = (
        _optional_json_body()
    )

    if error_response is not None:
        return error_response

    try:
        result = mark_chat_read(
            user_id=(
                g.current_user[
                    "_id"
                ]
            ),
            conversation_id=(
                conversation_id
            ),
            through_sequence=(
                body.get(
                    "through_sequence"
                )
            ),
        )

    except ChatServiceError as exc:
        return _service_error(
            exc
        )

    return jsonify(
        result
    )


__all__ = [
    "chats_bp",
]