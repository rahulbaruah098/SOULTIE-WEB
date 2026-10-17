"""Transaction boundary tests for cafe self-registration.

Simulated transaction writes are committed together or discarded
together. No running MongoDB instance is accessed.
"""

from copy import deepcopy
from types import SimpleNamespace

from bson import ObjectId
from pymongo.errors import (
    DuplicateKeyError,
    OperationFailure,
)
import pytest
from werkzeug.security import check_password_hash

from app.schemas.venue import VenueValidationError
from app.services import cafe_registration_service as service
from app.services.cafe_partner_auth_service import (
    CafePartnerAlreadyRegistered,
    CafePartnerAuthError,
)


PASSWORD = "SecureCafePassword2026!"


def registration(**changes):
    data = {
        "owner_name": "Cafe Owner",
        "owner_email": "OWNER@Example.com",
        "owner_phone": "9876543210",
        "venue_name": "Cafe Hills",
        "address": "Main Market Road",
        "city": "Guwahati",
        "state": "Assam",
        "postal_code": "781001",
    }
    data.update(changes)
    return data


class FakeInsertCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name
        self.fail = None
        self.calls = []

    def insert_one(self, document, *, session):
        if session is not self.database.session:
            raise AssertionError("Missing transaction session")

        self.calls.append(deepcopy(document))

        if self.fail is not None:
            raise self.fail

        identifier = document["_id"]

        if self.name == "accounts":
            email = document["email"]
            if any(
                old["email"] == email
                for old in self.database.committed["accounts"]
            ):
                raise DuplicateKeyError("duplicate email")

        self.database.pending[self.name].append(deepcopy(document))
        return SimpleNamespace(inserted_id=identifier)


class FakeSession:
    def __init__(self, database):
        self.database = database
        self.transaction_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def with_transaction(self, callback):
        self.transaction_count += 1

        self.database.pending = {
            "accounts": [],
            "venues": [],
        }

        try:
            result = callback(self)
        except Exception:
            self.database.pending = {
                "accounts": [],
                "venues": [],
            }
            raise

        for name in ("accounts", "venues"):
            self.database.committed[name].extend(
                self.database.pending[name]
            )

        self.database.pending = {
            "accounts": [],
            "venues": [],
        }
        return result


class FakeMongo:
    def __init__(self):
        self.committed = {
            "accounts": [],
            "venues": [],
        }
        self.pending = {
            "accounts": [],
            "venues": [],
        }
        self.session = FakeSession(self)
        self.accounts = FakeInsertCollection(self, "accounts")
        self.venues = FakeInsertCollection(self, "venues")
        self.db = SimpleNamespace(
            cafe_partner_accounts=self.accounts,
            venues=self.venues,
        )
        self.cx = SimpleNamespace(
            start_session=lambda: self.session
        )


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeMongo()
    monkeypatch.setattr(service, "mongo", fake)
    return fake


def test_creates_linked_pending_records(fake_db):
    result = service.register_cafe_with_venue(
        registration=registration(),
        password=PASSWORD,
    )

    assert result["status"] == "pending"
    assert result["email_verified"] is False

    assert len(fake_db.committed["accounts"]) == 1
    assert len(fake_db.committed["venues"]) == 1

    account = fake_db.committed["accounts"][0]
    venue = fake_db.committed["venues"][0]

    assert str(account["_id"]) == result["partner_id"]
    assert str(venue["_id"]) == result["venue_id"]

    assert account["venue_id"] == venue["_id"]
    assert venue["owner_id"] == str(account["_id"])

    assert account["account_type"] == "cafe_partner"
    assert account["status"] == "pending"
    assert account["email_verified"] is False
    assert venue["status"] == "pending"
    assert venue["active"] is False
    assert venue["status_version"] == 0

    assert account["email"] == "owner@example.com"
    assert account["password_hash"] != PASSWORD
    assert check_password_hash(
        account["password_hash"],
        PASSWORD,
    )

    assert fake_db.session.transaction_count == 1


def test_password_never_stored_on_venue(fake_db):
    service.register_cafe_with_venue(
        registration=registration(),
        password=PASSWORD,
    )

    venue = fake_db.committed["venues"][0]
    assert "password" not in venue
    assert "password_hash" not in venue
    assert "email_verified" not in venue


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_email", "invalid-email"),
        ("owner_phone", "not-a-number"),
        ("venue_name", ""),
        ("city", ""),
        ("state", ""),
    ],
)
def test_invalid_registration_makes_no_writes(
    fake_db, field, value
):
    with pytest.raises(VenueValidationError):
        service.register_cafe_with_venue(
            registration=registration(**{field: value}),
            password=PASSWORD,
        )

    assert not fake_db.accounts.calls
    assert not fake_db.venues.calls
    assert fake_db.session.transaction_count == 0


def test_invalid_password_makes_no_writes(fake_db):
    with pytest.raises(CafePartnerAuthError):
        service.register_cafe_with_venue(
            registration=registration(),
            password="short",
        )

    assert not fake_db.accounts.calls
    assert not fake_db.venues.calls


def test_venue_insert_failure_rolls_back_account(fake_db):
    fake_db.venues.fail = OperationFailure(
        "simulated venue insert failure"
    )

    with pytest.raises(OperationFailure):
        service.register_cafe_with_venue(
            registration=registration(),
            password=PASSWORD,
        )

    assert len(fake_db.accounts.calls) == 1
    assert len(fake_db.venues.calls) == 1
    assert fake_db.committed["accounts"] == []
    assert fake_db.committed["venues"] == []


def test_account_insert_failure_rolls_back(fake_db):
    fake_db.accounts.fail = OperationFailure(
        "simulated account insert failure"
    )

    with pytest.raises(OperationFailure):
        service.register_cafe_with_venue(
            registration=registration(),
            password=PASSWORD,
        )

    assert len(fake_db.accounts.calls) == 1
    assert len(fake_db.venues.calls) == 0
    assert fake_db.committed["accounts"] == []
    assert fake_db.committed["venues"] == []


def test_duplicate_email_cannot_create_second_venue(fake_db):
    service.register_cafe_with_venue(
        registration=registration(),
        password=PASSWORD,
    )

    with pytest.raises(CafePartnerAlreadyRegistered):
        service.register_cafe_with_venue(
            registration=registration(
                venue_name="Another Cafe",
                owner_email="owner@example.com",
            ),
            password=PASSWORD,
        )

    assert len(fake_db.committed["accounts"]) == 1
    assert len(fake_db.committed["venues"]) == 1


def test_transaction_start_failure_does_not_write(
    fake_db, monkeypatch
):
    def broken_start():
        raise OperationFailure(
            "Transactions unavailable in this deployment"
        )

    monkeypatch.setattr(
        fake_db.cx,
        "start_session",
        broken_start,
    )

    with pytest.raises(OperationFailure):
        service.register_cafe_with_venue(
            registration=registration(),
            password=PASSWORD,
        )

    assert not fake_db.accounts.calls
    assert not fake_db.venues.calls


def test_callback_does_not_commit_partial_records(fake_db):
    fake_db.venues.fail = DuplicateKeyError(
        "duplicate venue identifier"
    )

    with pytest.raises(CafePartnerAlreadyRegistered):
        service.register_cafe_with_venue(
            registration=registration(),
            password=PASSWORD,
        )

    assert fake_db.committed["accounts"] == []
    assert fake_db.committed["venues"] == []
