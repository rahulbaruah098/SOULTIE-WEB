"""FCM transport using an existing Firebase Admin application.

No implicit Firebase initialization or credential discovery occurs.
"""

class SafeMeetFCMConfigurationError(RuntimeError):
    pass


def send_safe_meet_fcm(token, payload, *, messaging_client=None):
    if not isinstance(token, str) or not token.strip():
        raise ValueError("FCM device token required.")
    if not isinstance(payload, dict):
        raise ValueError("Notification payload required.")

    title = payload.get("title")
    body = payload.get("body")
    data = payload.get("data")

    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or not isinstance(data, dict)
        or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in data.items()
        )
    ):
        raise ValueError("Invalid FCM message payload.")

    if messaging_client is None:
        try:
            import firebase_admin
            from firebase_admin import messaging
        except ImportError as exc:
            raise SafeMeetFCMConfigurationError(
                "Firebase Admin SDK is not installed."
            ) from exc

        try:
            firebase_admin.get_app()
        except ValueError as exc:
            raise SafeMeetFCMConfigurationError(
                "Firebase Admin application is not initialized."
            ) from exc

        messaging_client = messaging

    message = messaging_client.Message(
        token=token,
        notification=messaging_client.Notification(
            title=title,
            body=body,
        ),
        data=data,
    )

    message_id = messaging_client.send(message)
    return isinstance(message_id, str) and bool(message_id)
