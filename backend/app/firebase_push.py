import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]


def init_firebase():
    """Initialize Firebase Admin once per process."""

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    load_dotenv(BACKEND_DIR / ".env", override=False)

    credential_path = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_PATH"
    )

    if not credential_path:
        raise RuntimeError(
            "Firebase service-account path not configured."
        )

    path = Path(credential_path)

    if not path.is_file():
        raise RuntimeError(
            "Firebase service-account file not found."
        )

    cred = credentials.Certificate(str(path))

    return firebase_admin.initialize_app(cred)


def send_push_to_token(
    token,
    title,
    body,
    data=None
):
    """Send a push notification to one device."""

    if not isinstance(token, str) or not token.strip():
        raise ValueError("Valid FCM token required.")

    if not isinstance(title, str) or not title.strip():
        raise ValueError("Notification title required.")

    if not isinstance(body, str):
        raise ValueError("Notification body must be text.")

    if data is not None and not isinstance(data, dict):
        raise ValueError("Notification data must be a dictionary.")

    payload = {
        str(key): str(value)
        for key, value in (data or {}).items()
    }

    firebase_app = init_firebase()

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(
            title=title,
            body=body
        ),
        data=payload
    )

    return messaging.send(
        message,
        app=firebase_app
    )
