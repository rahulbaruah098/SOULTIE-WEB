"""Controlled SOULTIE FCM acceptance without starting Celery."""

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT = "soultie-a0487"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirm-send", action="store_true")
    args = parser.parse_args()

    print("=== SOULTIE FCM ACCEPTANCE ===")

    token = os.getenv("FCM_TEST_DEVICE_TOKEN", "").strip()

    if not token:
        print("RESULT: DEVICE TOKEN REQUIRED")
        print("Set FCM_TEST_DEVICE_TOKEN in this terminal.")
        print("No notification sent.")
        return 2

    if args.send and not args.confirm_send:
        print("STOP: Explicit --confirm-send required.")
        return 2

    credentials, project = google.auth.default(
        scopes=[SCOPE]
    )

    credential_project = (
        project or getattr(credentials, "project_id", None)
    )

    if credential_project != PROJECT:
        print("STOP: Firebase project mismatch.")
        return 2

    session = AuthorizedSession(credentials)

    endpoint = (
        f"https://fcm.googleapis.com/v1/projects/"
        f"{PROJECT}/messages:send"
    )

    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": "SOULTIE FCM Test",
                "body": "Backend notification delivery test."
            },
            "data": {
                "type": "fcm_test",
                "screen": "notifications"
            }
        }
    }

    if not args.send:
        payload["validate_only"] = True
        print("Mode: VALIDATION ONLY")
    else:
        print("Mode: EXPLICIT LIVE TEST")

    try:
        response = session.post(
            endpoint,
            json=payload,
            timeout=20
        )
    except Exception as exc:
        print("RESULT: NETWORK FAILURE")
        print("Error type:", type(exc).__name__)
        return 1

    print("HTTP status:", response.status_code)

    if response.status_code == 200:
        if args.send:
            print("RESULT: FCM MESSAGE ACCEPTED")
            print("Device display still requires verification.")
        else:
            print("RESULT: FCM VALIDATION PASSED")
            print("No notification delivered.")
        return 0

    try:
        error = response.json().get("error", {})
        print("FCM error category:", error.get("status", "UNKNOWN"))
    except (ValueError, AttributeError, TypeError):
        print("FCM error category: UNKNOWN")

    print("RESULT: FAILED")
    print("No credentials or device token displayed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
