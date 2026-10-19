"""Acceptance tests for Firebase unregistered-token classification."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.firebase_push_service import (
    FirebasePushError,
    FirebasePushService,
)


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (
            404,
            {
                "error": {
                    "details": [{
                        "@type": (
                            "type.googleapis.com/google.firebase."
                            "fcm.v1.FcmError"
                        ),
                        "errorCode": "UNREGISTERED",
                    }]
                }
            },
            "FCM_DEVICE_UNREGISTERED",
        ),
        (
            404,
            {"error": {"status": "NOT_FOUND"}},
            "FCM_TARGET_NOT_FOUND",
        ),
        (
            404,
            {"error": {"details": [{
                "@type": "type.googleapis.com/example.Other",
                "errorCode": "UNREGISTERED",
            }]}},
            "FCM_TARGET_NOT_FOUND",
        ),
        (
            503,
            {"error": {"status": "UNAVAILABLE"}},
            "FCM_PROVIDER_UNAVAILABLE",
        ),
    ],
)
def test_fcm_error_classification(status, payload, expected):
    credentials = MagicMock()
    credentials.valid = True
    credentials.token = "qa-test-access-token"

    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload

    session = MagicMock()
    session.post.return_value = response

    service = FirebasePushService(
        project_id="xuoroni-dev",
        session=session,
    )

    with patch(
        "app.services.firebase_push_service.google.auth.default",
        return_value=(credentials, "xuoroni-dev"),
    ):
        with pytest.raises(FirebasePushError) as error:
            service.send(
                token="a" * 40,
                title="Booking update",
                body="Your booking has changed",
                enabled=True,
            )

    assert error.value.code == expected
    assert session.post.call_count == 1