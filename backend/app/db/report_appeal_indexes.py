"""Idempotent report-appeal indexes."""

from pymongo import ASCENDING, DESCENDING


def ensure_report_appeal_indexes(db):
    db.report_appeals.create_index(
        [("report_id", ASCENDING), ("appellant_id", ASCENDING)],
        unique=True,
        name="report_appeal_unique_appellant",
    )
    db.report_appeals.create_index(
        [("status", ASCENDING), ("created_at", DESCENDING)],
        name="report_appeal_review_queue",
    )