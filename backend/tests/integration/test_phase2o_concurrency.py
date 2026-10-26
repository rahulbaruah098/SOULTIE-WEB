"""Concurrent moderation decisions must fail closed.

In-memory atomic collection simulates competing conditional updates.
No MongoDB connection or persistent data is used.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock

from bson import ObjectId
import pytest

from app.services import moderation_case_service as cases
from app.services import account_moderation_service as accounts


class AtomicCollection:
    def __init__(self, document):
        self.document = document
        self.lock = Lock()
        self.successes = 0

    def find_one_and_update(self, selector, update, **kwargs):
        with self.lock:
            for key, value in selector.items():
                if self.document.get(key) != value:
                    return None

            for key, value in update.get("$set", {}).items():
                self.document[key] = value

            for key, value in update.get("$push", {}).items():
                self.document.setdefault(key, []).append(value)

            self.successes += 1
            return dict(self.document)


def test_concurrent_case_decisions_have_single_winner(monkeypatch):
    report_id = ObjectId()
    collection = AtomicCollection({
        "_id": report_id,
        "reporter_id": ObjectId(),
        "target_user_id": ObjectId(),
        "reason": "spam",
        "description": "Repeated unwanted messages",
        "status": "submitted",
        "severity": 0,
        "created_at": datetime.now(timezone.utc),
    })

    class DB:
        reports = collection

    class Mongo:
        db = DB()

    monkeypatch.setattr(cases, "mongo", Mongo())
    barrier = Barrier(2)

    def decide(destination):
        barrier.wait(timeout=10)
        try:
            return cases.decide_case(
                ObjectId(),
                report_id,
                {
                    "expected_status": "submitted",
                    "decision": destination,
                    "reason": "Moderator reviewed the evidence",
                },
            )
        except cases.CaseError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(decide, "resolved"),
            pool.submit(decide, "dismissed"),
        ]
        results = [future.result(timeout=15) for future in futures]

    assert collection.successes == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("CASE_CONFLICT") == 1
    assert len(collection.document["review_history"]) == 1


def test_concurrent_account_actions_have_single_winner(monkeypatch):
    user_id = ObjectId()
    collection = AtomicCollection({
        "_id": user_id,
        "account_status": "active",
    })

    class DB:
        users = collection

    class Mongo:
        db = DB()

    monkeypatch.setattr(accounts, "mongo", Mongo())
    barrier = Barrier(2)

    def moderate(destination):
        barrier.wait(timeout=10)
        try:
            return accounts.moderate_account(
                ObjectId(),
                user_id,
                "active",
                destination,
                "Confirmed account safety policy violation",
            )
        except accounts.AccountModerationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(moderate, "suspended"),
            pool.submit(moderate, "banned"),
        ]
        results = [future.result(timeout=15) for future in futures]

    assert collection.successes == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("ACCOUNT_CONFLICT") == 1
    assert len(collection.document["account_moderation_history"]) == 1