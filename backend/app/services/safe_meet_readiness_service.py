
from datetime import datetime, timezone


def evaluate_safe_meet_readiness(
    *, database, public_https, monitoring_configured,
    backup_restore_verified, notification_delivery_enabled,
    emergency_dispatch_enabled,
):
    checks = {}

    hello = database.client.admin.command("hello")
    checks["mongo_transaction_topology"] = bool(
        hello.get("setName") or hello.get("msg") == "isdbgrid"
    )

    required = {
        "safe_meet_contact_consents": [
            ("safe_meet_id", 1), ("owner_id", 1)
        ],
        "safe_meet_contact_acceptances": [
            ("safe_meet_id", 1), ("owner_id", 1)
        ],
        "safe_meet_sos_events": [
            ("safe_meet_id", 1), ("owner_id", 1),
            ("idempotency_key", 1)
        ],
        "safe_meet_sos_escalations": [
            ("sos_event_id", 1)
        ],
    }

    for collection, fields in required.items():
        indexes = database[collection].index_information()
        checks["index_" + collection] = any(
            index.get("unique") is True
            and index.get("key") == fields
            for index in indexes.values()
        )

    location_indexes = (
        database.safe_meet_location_snapshots.index_information()
    )
    checks["location_expiry_ttl"] = any(
        index.get("key") == [("expires_at", 1)]
        and index.get("expireAfterSeconds") == 0
        for index in location_indexes.values()
    )

    checks["public_https"] = public_https is True
    checks["monitoring_configured"] = (
        monitoring_configured is True
    )
    checks["backup_restore_verified"] = (
        backup_restore_verified is True
    )
    checks["unverified_notifications_disabled"] = (
        notification_delivery_enabled is False
    )
    checks["emergency_dispatch_disabled"] = (
        emergency_dispatch_enabled is False
    )

    # Code-level checks are not substitutes for an actual
    # deployed HTTPS, monitoring or restore drill.
    backend_checks = (
        "mongo_transaction_topology",
        "location_expiry_ttl",
        "unverified_notifications_disabled",
        "emergency_dispatch_disabled",
        *("index_" + name for name in required),
    )
    backend_staging_candidate = all(
        checks[name] for name in backend_checks
    )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "backend_staging_candidate": backend_staging_candidate,
        "production_approved": False,
        "approval_reason": (
            "Requires deployed infrastructure, monitoring, "
            "restore-drill and operational validation."
        ),
    }
