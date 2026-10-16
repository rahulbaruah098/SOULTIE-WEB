"""Permanent Notification email policy regression tests."""

from bson import ObjectId
import pytest

import app.services.notification_email_policy as policy


def identity(owner, email, verified=True):
    return {
        "user_id": owner,
        "email_normalized": email,
        "verified": verified,
    }


def test_unique_verified_email(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(owner, " USER@EXAMPLE.COM "),
            identity(owner, "user@example.com"),
            identity(
                owner,
                "unverified@example.com",
                False,
            ),
        ],
    )

    assert policy.resolve_verified_notification_email(
        owner
    ) == "user@example.com"


def test_no_verified_email(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(
                owner,
                "wrong@example.com",
                False,
            )
        ],
    )

    with pytest.raises(
        policy.NotificationEmailSuppressed,
        match="NO_VERIFIED_EMAIL",
    ):
        policy.resolve_verified_notification_email(owner)


def test_ambiguous_verified_emails(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(owner, "one@example.com"),
            identity(owner, "two@example.com"),
        ],
    )

    with pytest.raises(
        policy.NotificationEmailSuppressed,
        match="AMBIGUOUS_VERIFIED_EMAIL",
    ):
        policy.resolve_verified_notification_email(owner)


def test_other_user_identity_ignored(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(
                ObjectId(),
                "other@example.com",
            )
        ],
    )

    with pytest.raises(
        policy.NotificationEmailSuppressed,
        match="NO_VERIFIED_EMAIL",
    ):
        policy.resolve_verified_notification_email(owner)


@pytest.mark.parametrize(
    "invalid",
    [None, "", "invalid-id", False],
)
def test_invalid_user_id(monkeypatch, invalid):
    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [],
    )

    with pytest.raises(ValueError):
        policy.resolve_verified_notification_email(invalid)


def test_email_uses_only_canonical_copy(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(owner, "verified@example.com")
        ],
    )

    output = policy.prepare_notification_email(
        {
            "user_id": owner,
            "type": "new_message",
            "title": "PRIVATE TITLE",
            "body": "PRIVATE MESSAGE BODY",
            "push_token": "PRIVATE TOKEN",
        }
    )

    assert output["recipient"] == "verified@example.com"
    assert output["subject"] == "Xuoroni: New message"

    assert (
        "You have a message to view."
        in output["text_body"]
    )

    combined = repr(output)

    for secret in (
        "PRIVATE TITLE",
        "PRIVATE MESSAGE BODY",
        "PRIVATE TOKEN",
    ):
        assert secret not in combined


def test_unknown_type_rejected(monkeypatch):
    owner = ObjectId()

    monkeypatch.setattr(
        policy,
        "get_identities_for_user",
        lambda user: [
            identity(owner, "verified@example.com")
        ],
    )

    with pytest.raises(ValueError):
        policy.prepare_notification_email(
            {
                "user_id": owner,
                "type": "unknown_type",
            }
        )
