"""Tests for Xuoroni compatibility access control."""

import pytest
from bson import ObjectId

from app.services import compatibility_service
from app.services.compatibility_service import (
    CompatibilityLifecycleError,
    CompatibilityNotFoundError,
    CompatibilityValidationError,
    get_profile_compatibility,
)


def _active_user(
    **overrides,
):
    user = {
        "_id": ObjectId(),
        "account_status": "active",
    }

    user.update(
        overrides
    )

    return user


def _active_profile(
    user_id,
    *,
    visibility="visible",
    **overrides,
):
    profile = {
        "_id": ObjectId(),
        "user_id": user_id,
        "onboarding_status": "completed",
        "profile_status": "active",
        "visibility": visibility,
        "relationship_intentions": [
            "long_term",
        ],
        "interests": [
            "Music",
        ],
        "languages": [
            "Assamese",
        ],
        "culture": {
            "communities": [],
            "tribes": [],
            "heritage_tags": [],
            "festivals": [],
            "custom_entries": [],
        },
        "lifestyle": {},
        "privacy": {
            "show_community": True,
            "show_distance": True,
        },
    }

    profile.update(
        overrides
    )

    return profile


def _install_pair(
    monkeypatch,
    *,
    requester_id,
    target_id,
    requester_user=None,
    target_user=None,
    requester_profile=None,
    target_profile=None,
    blocked=False,
    match=None,
    compatibility_result=None,
):
    if requester_user is None:
        requester_user = _active_user(
            _id=requester_id
        )

    if target_user is None:
        target_user = _active_user(
            _id=target_id
        )

    if requester_profile is None:
        requester_profile = (
            _active_profile(
                requester_id
            )
        )

    if target_profile is None:
        target_profile = (
            _active_profile(
                target_id
            )
        )

    def fake_get_user_by_id(
        user_id,
    ):
        if user_id == requester_id:
            return requester_user

        if user_id == target_id:
            return target_user

        return None

    def fake_get_profile_by_user_id(
        user_id,
    ):
        if user_id == requester_id:
            return requester_profile

        if user_id == target_id:
            return target_profile

        return None

    monkeypatch.setattr(
        compatibility_service,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    monkeypatch.setattr(
        compatibility_service,
        "get_profile_by_user_id",
        fake_get_profile_by_user_id,
    )

    monkeypatch.setattr(
        compatibility_service,
        "pair_is_blocked",
        lambda first, second: blocked,
    )

    monkeypatch.setattr(
        compatibility_service,
        "get_match_between",
        lambda first, second: match,
    )

    if compatibility_result is None:
        compatibility_result = {
            "score": 88,
            "target_user_id": str(
                target_id
            ),
        }

    monkeypatch.setattr(
        compatibility_service,
        "calculate_profile_compatibility",
        lambda first, second: compatibility_result,
    )

    return {
        "requester_user": requester_user,
        "target_user": target_user,
        "requester_profile": requester_profile,
        "target_profile": target_profile,
        "compatibility_result": (
            compatibility_result
        ),
    }


def test_invalid_requester_id_is_rejected():
    with pytest.raises(
        CompatibilityValidationError
    ) as exc_info:
        get_profile_compatibility(
            requester_id="not-an-object-id",
            target_user_id=ObjectId(),
        )

    error = exc_info.value

    assert error.code == "INVALID_USER_ID"
    assert error.status_code == 400


def test_invalid_target_id_is_rejected():
    with pytest.raises(
        CompatibilityValidationError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=ObjectId(),
            target_user_id="bad-target-id",
        )

    error = exc_info.value

    assert error.code == "INVALID_USER_ID"
    assert error.status_code == 400


def test_self_compatibility_is_rejected():
    user_id = ObjectId()

    with pytest.raises(
        CompatibilityValidationError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=user_id,
            target_user_id=user_id,
        )

    error = exc_info.value

    assert (
        error.code
        == "SELF_COMPATIBILITY_NOT_ALLOWED"
    )

    assert error.status_code == 400


def test_missing_requester_user_is_rejected(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    monkeypatch.setattr(
        compatibility_service,
        "get_user_by_id",
        lambda user_id: (
            None
            if user_id == requester_id
            else _active_user(
                _id=target_id
            )
        ),
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    error = exc_info.value

    assert (
        error.code
        == "CURRENT_USER_NOT_FOUND"
    )

    assert error.status_code == 404


def test_inactive_requester_account_is_rejected(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        requester_user=_active_user(
            _id=requester_id,
            account_status="paused",
        ),
    )

    with pytest.raises(
        CompatibilityLifecycleError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    error = exc_info.value

    assert (
        error.code
        == "CURRENT_ACCOUNT_INACTIVE"
    )

    assert error.status_code == 403


@pytest.mark.parametrize(
    "target_user",
    [
        None,
        {
            "_id": ObjectId(),
            "account_status": "paused",
        },
        {
            "_id": ObjectId(),
            "account_status": "banned",
        },
    ],
)
def test_unavailable_target_account_fails_closed(
    monkeypatch,
    target_user,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    requester_user = _active_user(
        _id=requester_id
    )

    def fake_get_user_by_id(
        user_id,
    ):
        if user_id == requester_id:
            return requester_user

        if user_id == target_id:
            return target_user

        return None

    monkeypatch.setattr(
        compatibility_service,
        "get_user_by_id",
        fake_get_user_by_id,
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    error = exc_info.value

    assert (
        error.code
        == "COMPATIBILITY_NOT_AVAILABLE"
    )

    assert error.status_code == 404


def test_missing_requester_profile_is_rejected(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    pair = _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
    )

    def fake_profiles(
        user_id,
    ):
        if user_id == requester_id:
            return None

        return pair[
            "target_profile"
        ]

    monkeypatch.setattr(
        compatibility_service,
        "get_profile_by_user_id",
        fake_profiles,
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "CURRENT_PROFILE_NOT_FOUND"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


def test_incomplete_requester_onboarding_is_rejected(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        requester_profile=_active_profile(
            requester_id,
            onboarding_status=(
                "in_progress"
            ),
        ),
    )

    with pytest.raises(
        CompatibilityLifecycleError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "ONBOARDING_INCOMPLETE"
    )

    assert (
        exc_info.value.status_code
        == 409
    )


def test_inactive_requester_profile_is_rejected(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        requester_profile=_active_profile(
            requester_id,
            profile_status="paused",
        ),
    )

    with pytest.raises(
        CompatibilityLifecycleError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "CURRENT_PROFILE_INACTIVE"
    )

    assert (
        exc_info.value.status_code
        == 403
    )


@pytest.mark.parametrize(
    "target_profile",
    [
        None,
        {
            "onboarding_status": (
                "in_progress"
            ),
            "profile_status": "active",
            "visibility": "visible",
        },
        {
            "onboarding_status": (
                "completed"
            ),
            "profile_status": "disabled",
            "visibility": "visible",
        },
    ],
)
def test_unavailable_target_profile_fails_closed(
    monkeypatch,
    target_profile,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    requester_user = _active_user(
        _id=requester_id
    )

    target_user = _active_user(
        _id=target_id
    )

    requester_profile = (
        _active_profile(
            requester_id
        )
    )

    def fake_user(
        user_id,
    ):
        if user_id == requester_id:
            return requester_user

        if user_id == target_id:
            return target_user

        return None

    def fake_profile(
        user_id,
    ):
        if user_id == requester_id:
            return requester_profile

        if user_id == target_id:
            return target_profile

        return None

    monkeypatch.setattr(
        compatibility_service,
        "get_user_by_id",
        fake_user,
    )

    monkeypatch.setattr(
        compatibility_service,
        "get_profile_by_user_id",
        fake_profile,
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "COMPATIBILITY_NOT_AVAILABLE"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


def test_blocked_pair_fails_closed(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        blocked=True,
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "COMPATIBILITY_NOT_AVAILABLE"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


@pytest.mark.parametrize(
    "visibility",
    [
        "hidden",
        "paused",
    ],
)
@pytest.mark.parametrize(
    "match",
    [
        None,
        {
            "status": "unmatched",
        },
    ],
)
def test_non_visible_target_requires_active_match(
    monkeypatch,
    visibility,
    match,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        target_profile=_active_profile(
            target_id,
            visibility=visibility,
        ),
        match=match,
    )

    with pytest.raises(
        CompatibilityNotFoundError
    ) as exc_info:
        get_profile_compatibility(
            requester_id=requester_id,
            target_user_id=target_id,
        )

    assert (
        exc_info.value.code
        == "COMPATIBILITY_NOT_AVAILABLE"
    )

    assert (
        exc_info.value.status_code
        == 404
    )


@pytest.mark.parametrize(
    "visibility",
    [
        "hidden",
        "paused",
    ],
)
def test_active_match_can_access_non_visible_target(
    monkeypatch,
    visibility,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    expected = {
        "score": 91,
        "target_user_id": str(
            target_id
        ),
    }

    pair = _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        target_profile=_active_profile(
            target_id,
            visibility=visibility,
        ),
        match={
            "status": "active",
        },
        compatibility_result=expected,
    )

    result = get_profile_compatibility(
        requester_id=requester_id,
        target_user_id=target_id,
    )

    assert result == expected

    assert (
        pair[
            "target_profile"
        ]["visibility"]
        == visibility
    )


def test_visible_target_does_not_require_match(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    expected = {
        "score": 84,
        "target_user_id": str(
            target_id
        ),
    }

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        compatibility_result=expected,
    )

    def fail_if_match_checked(
        first,
        second,
    ):
        raise AssertionError(
            (
                "Visible targets must not "
                "require a match lookup."
            )
        )

    monkeypatch.setattr(
        compatibility_service,
        "get_match_between",
        fail_if_match_checked,
    )

    result = get_profile_compatibility(
        requester_id=requester_id,
        target_user_id=target_id,
    )

    assert result == expected


def test_authorized_pair_passes_exact_profiles_to_scorer(
    monkeypatch,
):
    requester_id = ObjectId()
    target_id = ObjectId()

    requester_profile = (
        _active_profile(
            requester_id
        )
    )

    target_profile = (
        _active_profile(
            target_id
        )
    )

    captured = {}

    _install_pair(
        monkeypatch,
        requester_id=requester_id,
        target_id=target_id,
        requester_profile=(
            requester_profile
        ),
        target_profile=(
            target_profile
        ),
    )

    def fake_calculate(
        first,
        second,
    ):
        captured["first"] = first
        captured["second"] = second

        return {
            "score": 77,
            "target_user_id": str(
                target_id
            ),
        }

    monkeypatch.setattr(
        compatibility_service,
        "calculate_profile_compatibility",
        fake_calculate,
    )

    result = get_profile_compatibility(
        requester_id=str(
            requester_id
        ),
        target_user_id=str(
            target_id
        ),
    )

    assert (
        captured["first"]
        is requester_profile
    )

    assert (
        captured["second"]
        is target_profile
    )

    assert result["score"] == 77