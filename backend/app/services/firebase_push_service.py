"""FCM HTTP v1 transport for Xuoroni push notifications.

The caller supplies an authorized, server-side device token.

FCM acceptance is not proof of device delivery. FCM does not
provide durable exactly-once idempotency for this interface.

Do not use this class as the cafe booking recipient's
supports_durable_idempotency provider.
"""

import os
import re
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import Request
import requests


FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_DATA_BYTES = 4096
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9:_-]{20,4096}$")
_PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,62}$")


class FirebasePushError(RuntimeError):
    """FCM transport failure without exposing device credentials."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FirebasePushService:
    """Explicitly invoked FCM transport; never starts a worker."""

    # FCM does not supply durable provider-side idempotency.
    supports_durable_idempotency = False

    def __init__(self, *, project_id=None, session=None):
        self.project_id = (
            project_id
            if project_id is not None
            else os.getenv("FIREBASE_PROJECT_ID", "")
        )
        if (
            not isinstance(self.project_id, str)
            or not _PROJECT_PATTERN.fullmatch(self.project_id)
        ):
            raise FirebasePushError("FCM_PROJECT_NOT_CONFIGURED")

        self.session = session or requests

    def send(self, *, token, title, body, data=None, enabled=False):
        """Submit one push request and return its FCM message name.

        The caller must enforce recipient authorization,
        notification preferences, retry policy and deduplication.
        """
        if enabled is not True:
            raise FirebasePushError("FCM_DELIVERY_DISABLED")

        if (
            not isinstance(token, str)
            or not _TOKEN_PATTERN.fullmatch(token)
        ):
            raise FirebasePushError("FCM_INVALID_DEVICE_TOKEN")

        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 120
            or not isinstance(body, str)
            or not body.strip()
            or len(body) > 1000
        ):
            raise FirebasePushError("FCM_INVALID_NOTIFICATION")

        if data is None:
            data = {}

        if (
            not isinstance(data, dict)
            or any(
                not isinstance(k, str)
                or not k
                or not isinstance(v, str)
                for k, v in data.items()
            )
        ):
            raise FirebasePushError("FCM_INVALID_DATA")

        import json

        if (
            len(
                json.dumps(
                    data, ensure_ascii=False
                ).encode("utf-8")
            ) > MAX_DATA_BYTES
        ):
            raise FirebasePushError("FCM_DATA_TOO_LARGE")

        try:
            credentials, _ = google.auth.default(
                scopes=[FCM_SCOPE]
            )
            if not credentials.valid:
                credentials.refresh(Request())

            access_token = credentials.token
            if not isinstance(access_token, str) or not access_token:
                raise FirebasePushError("FCM_AUTH_UNAVAILABLE")
        except FirebasePushError:
            raise
        except Exception:
            raise FirebasePushError("FCM_AUTH_UNAVAILABLE") from None

        endpoint = (
            "https://fcm.googleapis.com/v1/projects/"
            + quote(self.project_id, safe="")
            + "/messages:send"
        )

        payload = {
            "message": {
                "token": token,
                "notification": {
                    "title": title,
                    "body": body,
                },
                "data": data,
                "android": {
                    "priority": "high",
                },
            }
        }

        try:
            response = self.session.post(
                endpoint,
                headers={
                    "Authorization": "Bearer " + access_token,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=15,
            )
        except requests.RequestException:
            raise FirebasePushError("FCM_NETWORK_FAILURE") from None
        except Exception:
            raise FirebasePushError("FCM_TRANSPORT_FAILURE") from None

        if response.status_code != 200:
            if response.status_code in (401, 403):
                code = "FCM_AUTH_REJECTED"
            elif response.status_code == 404:
                code = "FCM_TARGET_NOT_FOUND"
                try:
                    error = response.json().get("error", {})
                    details = error.get("details", [])
                    if any(
                        isinstance(item, dict)
                        and item.get("errorCode") == "UNREGISTERED"
                        and item.get("@type")
                        == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
                        for item in details
                    ):
                        code = "FCM_DEVICE_UNREGISTERED"
                except (ValueError, TypeError, AttributeError):
                    pass
            elif response.status_code == 429:
                code = "FCM_RATE_LIMITED"
            elif response.status_code >= 500:
                code = "FCM_PROVIDER_UNAVAILABLE"
            else:
                code = "FCM_REQUEST_REJECTED"
            raise FirebasePushError(code)

        try:
            response_data = response.json()
            message_name = response_data.get("name")
        except (ValueError, AttributeError):
            raise FirebasePushError("FCM_INVALID_RESPONSE") from None

        if (
            not isinstance(message_name, str)
            or not message_name.startswith(
                "projects/" + self.project_id + "/messages/"
            )
            or len(message_name.split("/")[-1]) == 0
        ):
            raise FirebasePushError("FCM_INVALID_RESPONSE")

        return {
            "accepted": True,
            "message_name": message_name,
            "delivery_guarantee": "at_least_once_possible",
        }