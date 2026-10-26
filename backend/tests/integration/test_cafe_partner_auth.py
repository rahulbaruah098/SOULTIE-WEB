"""Security tests for Soultie caf? partner account foundation."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash

from app.services import cafe_partner_auth_service as service


PASSWORD = "CafeOwnerSecurePassword#2026"


@pytest.fixture
def registration():
    return {
        "owner_name": "Example Owner",
        "owner_email": "OWNER@EXAMPLE.COM",
        "owner_phone": "+919876543210",
        "venue_name": "Example Cafe",
        "address": "Main Road",
        "city": "Guwahati",
        "state": "Assam",
    }


@pytest.fixture
def collection(monkeypatch):
    class FakeCollection:
        def __init__(self):
            self.saved = None
            self.account = None
            self.last_query = None
            self.duplicate = False

        def insert_one(self, document):
            if self.duplicate:
                raise DuplicateKeyError("duplicate")

            self.saved = document.copy()
            return SimpleNamespace(inserted_id=ObjectId())

        def find_one(self, query):
            self.last_query = query.copy()
            return self.account

    fake = FakeCollection()

    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(cafe_partner_accounts=fake)
        ),
    )

    return fake


def test_registration_is_pending_and_secret_is_hashed(
    collection, registration
):
    partner_id = service.register_pending_cafe_partner(
        registration,
        password=PASSWORD,
    )

    assert isinstance(partner_id, ObjectId)
    assert collection.saved["status"] == "pending"
    assert collection.saved["account_type"] == "cafe_partner"
    assert collection.saved["email"] == "owner@example.com"
    assert collection.saved["email_verified"] is False
    assert collection.saved["venue_id"] is None
    assert collection.saved["password_hash"] != PASSWORD
    assert check_password_hash(
        collection.saved["password_hash"],
        PASSWORD,
    )


@pytest.mark.parametrize(
    "password",
    [
        "",
        "short",
        "x" * 129,
        "Invalid" + chr(10) + "Password",
        None,
        123456789012,
    ],
)
def test_reject_invalid_registration_passwords(
    collection, registration, password
):
    with pytest.raises(service.CafePartnerAuthError):
        service.register_pending_cafe_partner(
            registration,
            password=password,
        )

    assert collection.saved is None


def test_registration_rejects_caller_selected_status(
    collection, registration
):
    registration["status"] = "approved"

    with pytest.raises(ValueError):
        service.register_pending_cafe_partner(
            registration,
            password=PASSWORD,
        )

    assert collection.saved is None


def test_registration_requires_timezone_aware_time(
    collection, registration
):
    with pytest.raises(service.CafePartnerAuthError):
        service.register_pending_cafe_partner(
            registration,
            password=PASSWORD,
            now=datetime(2026, 10, 16),
        )


def test_duplicate_email_maps_to_safe_error(
    collection, registration
):
    collection.duplicate = True

    with pytest.raises(service.CafePartnerAlreadyRegistered):
        service.register_pending_cafe_partner(
            registration,
            password=PASSWORD,
        )


@pytest.fixture
def approved_account(collection, registration):
    service.register_pending_cafe_partner(
        registration,
        password=PASSWORD,
        now=datetime(
            2026, 10, 16, tzinfo=timezone.utc
        ),
    )

    account = {
        "_id": ObjectId(),
        "venue_id": ObjectId(),
        "account_type": "cafe_partner",
        "status": "approved",
        "email_verified": True,
        "password_hash": collection.saved["password_hash"],
    }

    collection.account = account
    return account


def test_approved_verified_partner_can_authenticate(
    collection, approved_account
):
    response = service.authenticate_cafe_partner_password(
        email="OWNER@EXAMPLE.COM",
        password=PASSWORD,
    )

    assert response == {
        "partner_id": str(approved_account["_id"]),
        "venue_id": str(approved_account["venue_id"]),
        "account_type": "cafe_partner",
        "email": "owner@example.com",
    }

    assert collection.last_query == {
        "email": "owner@example.com",
        "account_type": "cafe_partner",
    }


@pytest.mark.parametrize(
    "status",
    ["pending", "rejected", "suspended"],
)
def test_unapproved_partner_cannot_authenticate(
    approved_account, status
):
    approved_account["status"] = status

    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email="owner@example.com",
            password=PASSWORD,
        )


def test_unverified_partner_cannot_authenticate(
    approved_account
):
    approved_account["email_verified"] = False

    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email="owner@example.com",
            password=PASSWORD,
        )


def test_partner_without_linked_venue_cannot_authenticate(
    approved_account
):
    approved_account["venue_id"] = None

    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email="owner@example.com",
            password=PASSWORD,
        )


def test_wrong_password_fails(approved_account):
    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email="owner@example.com",
            password="WrongPassword#2026",
        )


def test_missing_account_fails(collection):
    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email="owner@example.com",
            password=PASSWORD,
        )


@pytest.mark.parametrize(
    "email",
    ["", "not-an-email", "owner@", "@example.com", None],
)
def test_invalid_email_rejected(email):
    with pytest.raises(service.CafePartnerAuthError):
        service.authenticate_cafe_partner_password(
            email=email,
            password=PASSWORD,
        )


def test_dating_and_admin_accounts_not_queried(
    collection, approved_account
):
    service.authenticate_cafe_partner_password(
        email="owner@example.com",
        password=PASSWORD,
    )

    assert collection.last_query["account_type"] == "cafe_partner"
    assert not hasattr(
        collection,
        "users",
    )
    assert not hasattr(
        collection,
        "admins",
    )


class FakeCafeAccountLinkUpdates:
    def __init__(self):
        self.last_query = None
        self.last_update = None
        self.matched_count = 1
        self.update_calls = 0

    def update_one(self, query, update):
        self.last_query = query
        self.last_update = update
        self.update_calls += 1
        return SimpleNamespace(matched_count=self.matched_count)


@pytest.fixture
def linking_context(monkeypatch):
    from app.repositories import venue_repository

    owner_id = ObjectId()
    venue_id = ObjectId()
    updates = FakeCafeAccountLinkUpdates()

    fake_venue = {
        "_id": venue_id,
        "owner_id": str(owner_id),
        "status": "pending",
    }

    calls = []

    def get_owned_venue(requested_id, *, owner_id):
        calls.append((requested_id, owner_id))
        return fake_venue

    monkeypatch.setattr(
        venue_repository,
        "get_owned_venue",
        get_owned_venue,
    )

    monkeypatch.setattr(
        service,
        "mongo",
        SimpleNamespace(
            db=SimpleNamespace(cafe_partner_accounts=updates)
        ),
    )

    return SimpleNamespace(
        owner_id=owner_id,
        venue_id=venue_id,
        venue=fake_venue,
        updates=updates,
        lookups=calls,
    )


def test_linking_pending_owned_venue(linking_context):
    ctx = linking_context

    result = service.link_pending_cafe_partner_venue(
        partner_id=str(ctx.owner_id),
        venue_id=str(ctx.venue_id),
    )

    assert result == {
        "partner_id": str(ctx.owner_id),
        "venue_id": str(ctx.venue_id),
        "status": "pending",
    }
    assert ctx.lookups == [
        (ctx.venue_id, str(ctx.owner_id))
    ]
    assert ctx.updates.last_query == {
        "_id": ctx.owner_id,
        "account_type": "cafe_partner",
        "status": "pending",
        "venue_id": None,
    }
    assert ctx.updates.last_update["$set"]["venue_id"] == ctx.venue_id
    assert ctx.updates.update_calls == 1


def test_linking_does_not_accept_other_owner(linking_context):
    ctx = linking_context
    ctx.venue["owner_id"] = str(ObjectId())

    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=ctx.owner_id,
            venue_id=ctx.venue_id,
        )

    assert ctx.updates.update_calls == 0


def test_linking_rejects_missing_venue(
    linking_context, monkeypatch
):
    from app.repositories import venue_repository

    monkeypatch.setattr(
        venue_repository,
        "get_owned_venue",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=linking_context.owner_id,
            venue_id=linking_context.venue_id,
        )

    assert linking_context.updates.update_calls == 0


@pytest.mark.parametrize(
    "status",
    ["approved", "rejected", "suspended"],
)
def test_linking_rejects_nonpending_venue(
    linking_context, status
):
    ctx = linking_context
    ctx.venue["status"] = status

    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=ctx.owner_id,
            venue_id=ctx.venue_id,
        )

    assert ctx.updates.update_calls == 0


@pytest.mark.parametrize(
    "partner_id,venue_id",
    [
        ("invalid", str(ObjectId())),
        (str(ObjectId()), "invalid"),
        (None, str(ObjectId())),
        (str(ObjectId()), None),
        (123, str(ObjectId())),
    ],
)
def test_linking_rejects_invalid_identifiers(
    linking_context, partner_id, venue_id
):
    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=partner_id,
            venue_id=venue_id,
        )

    assert linking_context.updates.update_calls == 0


def test_linking_rejects_naive_datetime(linking_context):
    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=linking_context.owner_id,
            venue_id=linking_context.venue_id,
            now=datetime(2026, 10, 16),
        )

    assert linking_context.updates.update_calls == 0


def test_linking_rejects_repeat_or_ineligible_account(
    linking_context
):
    ctx = linking_context
    ctx.updates.matched_count = 0

    with pytest.raises(service.CafePartnerAuthError):
        service.link_pending_cafe_partner_venue(
            partner_id=ctx.owner_id,
            venue_id=ctx.venue_id,
        )

    assert ctx.updates.update_calls == 1
    assert ctx.updates.last_query["venue_id"] is None
    assert ctx.updates.last_query["status"] == "pending"


def test_linking_normalizes_timezone(linking_context):
    from datetime import timedelta

    ctx = linking_context
    india_timezone = timezone(timedelta(hours=5, minutes=30))
    local_time = datetime(
        2026, 10, 16, 15, 30, tzinfo=india_timezone
    )

    service.link_pending_cafe_partner_venue(
        partner_id=ctx.owner_id,
        venue_id=ctx.venue_id,
        now=local_time,
    )

    assert ctx.updates.last_update["$set"]["updated_at"] == datetime(
        2026, 10, 16, 10, 0, tzinfo=timezone.utc
    )
