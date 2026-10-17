"""Permanent isolated tests for transactional cafe moderation."""

from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.services import cafe_moderation_service as moderation


class FakeSession:
    def __init__(self, fail=False):
        self.fail = fail
        self.transaction_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def with_transaction(self, callback):
        self.transaction_calls += 1
        if self.fail:
            raise RuntimeError("Transactions unavailable")
        return callback(self)


class FakeCollection:
    def __init__(self, document=None):
        self.document = document
        self.find_calls = []
        self.update_calls = []
        self.insert_calls = []
        self.matched_count = 1

    def find_one(self, query, *, session):
        assert isinstance(session, FakeSession)
        self.find_calls.append((query, session))
        if self.document is None:
            return None
        # Return a copy, as PyMongo would.
        return dict(self.document)

    def update_one(self, query, update, *, session):
        assert isinstance(session, FakeSession)
        self.update_calls.append((query, update, session))
        return SimpleNamespace(matched_count=self.matched_count)

    def insert_one(self, document, *, session):
        assert isinstance(session, FakeSession)
        self.insert_calls.append((document, session))
        return SimpleNamespace(inserted_id=ObjectId())


@pytest.fixture
def context(monkeypatch):
    admin_id = ObjectId()
    owner_id = ObjectId()
    venue_id = ObjectId()

    admins = FakeCollection({
        "_id": admin_id,
        "role": "super_admin",
        "status": "active",
        "is_active": True,
    })
    venues = FakeCollection({
        "_id": venue_id,
        "owner_id": str(owner_id),
        "status": "pending",
    })
    partners = FakeCollection({
        "_id": owner_id,
        "venue_id": venue_id,
        "account_type": "cafe_partner",
        "status": "pending",
        "email_verified": True,
    })
    audits = FakeCollection()
    session = FakeSession()

    database = SimpleNamespace(
        admins=admins,
        venues=venues,
        cafe_partner_accounts=partners,
        audit_logs=audits,
    )

    class FakeClient:
        def start_session(self):
            return session

    monkeypatch.setattr(
        moderation,
        "mongo",
        SimpleNamespace(db=database, cx=FakeClient()),
    )

    return SimpleNamespace(
        admin_id=admin_id,
        owner_id=owner_id,
        venue_id=venue_id,
        admins=admins,
        venues=venues,
        partners=partners,
        audits=audits,
        session=session,
    )


def decide(ctx, *, previous="pending", action="approved", reason=None):
    return moderation.moderate_cafe_venue(
        superadmin_id=ctx.admin_id,
        venue_id=ctx.venue_id,
        expected_status=previous,
        decision=action,
        reason=reason,
    )


def assert_no_writes(ctx):
    assert not ctx.venues.update_calls
    assert not ctx.partners.update_calls
    assert not ctx.audits.insert_calls


def test_approval_updates_both_records_and_writes_audit(context):
    ctx = context
    result = decide(ctx)

    assert result == {
        "venue_id": str(ctx.venue_id),
        "partner_id": str(ctx.owner_id),
        "previous_status": "pending",
        "status": "approved",
    }
    assert ctx.session.transaction_calls == 1

    assert len(ctx.venues.update_calls) == 1
    assert len(ctx.partners.update_calls) == 1
    assert len(ctx.audits.insert_calls) == 1

    venue_query, venue_update, session_a = ctx.venues.update_calls[0]
    account_query, account_update, session_b = ctx.partners.update_calls[0]
    audit, session_c = ctx.audits.insert_calls[0]

    assert session_a is ctx.session
    assert session_b is ctx.session
    assert session_c is ctx.session
    assert venue_query["status"] == "pending"
    assert account_query["status"] == "pending"
    assert venue_query["owner_id"] == str(ctx.owner_id)
    assert account_query["venue_id"] == ctx.venue_id
    assert venue_update["$set"]["status"] == "approved"
    assert account_update["$set"]["status"] == "approved"
    assert venue_update["$inc"]["status_version"] == 1
    assert audit["previous_status"] == "pending"
    assert audit["new_status"] == "approved"
    assert audit["actor_id"] == str(ctx.admin_id)
    assert audit["resource_id"] == str(ctx.venue_id)


@pytest.mark.parametrize(
    "previous,action,reason",
    [
        ("pending", "rejected", "Business details invalid"),
        ("approved", "suspended", "Owner compliance issue"),
        ("rejected", "approved", None),
        ("suspended", "approved", None),
    ],
)
def test_supported_status_transitions(
    context, previous, action, reason
):
    context.venues.document["status"] = previous
    context.partners.document["status"] = previous

    result = decide(
        context,
        previous=previous,
        action=action,
        reason=reason,
    )

    assert result["status"] == action
    assert len(context.venues.update_calls) == 1
    assert len(context.partners.update_calls) == 1
    assert len(context.audits.insert_calls) == 1


@pytest.mark.parametrize(
    "previous,action,reason",
    [
        ("pending", "suspended", "Not approved"),
        ("approved", "rejected", "Not a valid transition"),
        ("approved", "approved", None),
        ("pending", "rejected", None),
        ("approved", "suspended", ""),
        ("pending", "rejected", "x" * 1001),
        ("pending", "rejected", "invalid" + chr(10)),
        ("missing", "approved", None),
    ],
)
def test_invalid_transition_or_reason_is_rejected_before_transaction(
    context, previous, action, reason
):
    with pytest.raises(moderation.CafeModerationError):
        decide(
            context,
            previous=previous,
            action=action,
            reason=reason,
        )

    assert context.session.transaction_calls == 0
    assert_no_writes(context)


@pytest.mark.parametrize(
    "identity",
    ["invalid-id", "", None, 123],
)
def test_invalid_superadmin_id_rejected(context, identity):
    with pytest.raises(moderation.CafeModerationError):
        moderation.moderate_cafe_venue(
            superadmin_id=identity,
            venue_id=context.venue_id,
            expected_status="pending",
            decision="approved",
        )
    assert_no_writes(context)


@pytest.mark.parametrize(
    "admin_changes",
    [
        {"role": "admin"},
        {"role": None},
        {"status": "suspended"},
        {"is_active": False},
    ],
)
def test_non_superadmin_cannot_moderate(context, admin_changes):
    context.admins.document.update(admin_changes)
    with pytest.raises(moderation.CafeModerationError):
        decide(context)
    assert_no_writes(context)


def test_removed_superadmin_cannot_moderate(context):
    context.admins.document = None

    with pytest.raises(moderation.CafeModerationError):
        decide(context)

    assert_no_writes(context)


def test_stale_venue_status_rejected(context):
    context.venues.document["status"] = "approved"

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert_no_writes(context)


def test_wrong_owner_link_rejected(context):
    context.venues.document["owner_id"] = str(ObjectId())

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert_no_writes(context)


def test_missing_partner_account_rejected(context):
    context.partners.document = None

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert_no_writes(context)


def test_mismatched_partner_status_rejected(context):
    context.partners.document["status"] = "suspended"

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert_no_writes(context)


def test_concurrent_venue_change_rejected_before_partner_write(context):
    context.venues.matched_count = 0

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert len(context.venues.update_calls) == 1
    assert not context.partners.update_calls
    assert not context.audits.insert_calls


def test_concurrent_partner_change_rejected_before_audit(context):
    context.partners.matched_count = 0

    with pytest.raises(moderation.CafeModerationConflict):
        decide(context)

    assert len(context.venues.update_calls) == 1
    assert len(context.partners.update_calls) == 1
    assert not context.audits.insert_calls


def test_unavailable_transaction_fails_closed(context):
    context.session.fail = True

    with pytest.raises(RuntimeError, match="Transactions unavailable"):
        decide(context)

    assert context.session.transaction_calls == 1
    assert_no_writes(context)


def test_audit_insertion_failure_propagates(context):
    def fail_audit(*args, **kwargs):
        raise RuntimeError("Audit storage failed")

    context.audits.insert_one = fail_audit

    with pytest.raises(RuntimeError, match="Audit storage failed"):
        decide(context)

    assert context.session.transaction_calls == 1
    assert len(context.venues.update_calls) == 1
    assert len(context.partners.update_calls) == 1


@pytest.mark.parametrize("verified", [False, None])
def test_unverified_partner_cannot_be_approved(
    context, verified
):
    context.partners.document["email_verified"] = verified

    with pytest.raises(
        moderation.CafeModerationConflict,
        match="Verified cafe partner email",
    ):
        decide(context)

    assert_no_writes(context)


@pytest.mark.parametrize(
    "previous",
    ["rejected", "suspended"],
)
def test_unverified_partner_cannot_be_reactivated(
    context, previous
):
    context.venues.document["status"] = previous
    context.partners.document["status"] = previous
    context.partners.document["email_verified"] = False

    with pytest.raises(
        moderation.CafeModerationConflict,
        match="Verified cafe partner email",
    ):
        decide(
            context,
            previous=previous,
            action="approved",
        )

    assert_no_writes(context)


def test_unverified_partner_can_be_rejected(context):
    context.partners.document["email_verified"] = False

    result = decide(
        context,
        previous="pending",
        action="rejected",
        reason="Application details are incomplete",
    )

    assert result["status"] == "rejected"
    assert len(context.venues.update_calls) == 1
    assert len(context.partners.update_calls) == 1
    assert len(context.audits.insert_calls) == 1
