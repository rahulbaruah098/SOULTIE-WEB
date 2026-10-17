from pymongo import ASCENDING, DESCENDING, GEOSPHERE


def ensure_indexes(db) -> list[str]:
    """
    Create all Xuoroni MongoDB indexes required by the
    current backend foundation.

    MongoDB create_index operations are idempotent when
    the existing index definition matches.
    """

    created = []

    # =====================================================
    # LEGACY / EXISTING WEBSITE COLLECTIONS
    # =====================================================

    created.append(
        db.admins.create_index(
            [("email", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.testers.create_index(
            [("email", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.settings.create_index(
            [("key", ASCENDING)],
            unique=True,
        )
    )

    # =====================================================
    # USER / AUTHENTICATION
    # =====================================================

    created.append(
        db.users.create_index(
            [
                ("account_status", ASCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    created.append(
        db.auth_identities.create_index(
            [
                ("provider", ASCENDING),
                ("provider_subject", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.auth_identities.create_index(
            [("phone_normalized", ASCENDING)],
            unique=True,
            sparse=True,
        )
    )

    created.append(
        db.auth_identities.create_index(
            [("email_normalized", ASCENDING)],
            unique=True,
            sparse=True,
        )
    )

    created.append(
        db.auth_sessions.create_index(
            [("refresh_token_hash", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.auth_sessions.create_index(
            [("session_id", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.auth_sessions.create_index(
            [
                ("family_id", ASCENDING),
                ("revoked_at", ASCENDING),
            ]
        )
    )

    created.append(
        db.auth_sessions.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )
    )

    created.append(
        db.auth_sessions.create_index(
            [
                ("user_id", ASCENDING),
                ("revoked_at", ASCENDING),
            ]
        )
    )

    created.append(
        db.user_devices.create_index(
            [
                ("user_id", ASCENDING),
                ("device_id", ASCENDING),
            ],
            unique=True,
        )
    )


    # Active push tokens must have a single registered owner.
    # Revoked devices unset the token and its hash.
    created.append(
        db.user_devices.create_index(
            [("push_token_hash", ASCENDING)],
            unique=True,
            name="user_devices_active_push_token_unique",
            partialFilterExpression={
                "push_token_hash": {"$type": "string"},
            },
        )
    )

    # Fast selection of current push-capable user devices.
    created.append(
        db.user_devices.create_index(
            [
                ("user_id", ASCENDING),
                ("push_enabled", ASCENDING),
                ("revoked_at", ASCENDING),
            ],
            name="user_devices_push_owner_lookup",
        )
    )

    # =====================================================
    # PROFILE / DISCOVERY
    # =====================================================

    created.append(
        db.profiles.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.profiles.create_index(
            [("location", GEOSPHERE)]
        )
    )

    created.append(
        db.profiles.create_index(
            [
                ("onboarding_status", ASCENDING),
                ("visibility", ASCENDING),
                ("gender_identity", ASCENDING),
                ("birth_date", ASCENDING),
                ("last_active_at", DESCENDING),
            ]
        )
    )

    created.append(
        db.profiles.create_index(
            [
                ("surname_searchable", ASCENDING),
                ("surname_normalized", ASCENDING),
            ]
        )
    )

    created.append(
        db.discovery_preferences.create_index(
            [("user_id", ASCENDING)],
            unique=True,
        )
    )

    # =====================================================
    # PROFILE MEDIA
    # =====================================================

    created.append(
        db.profile_media.create_index(
            [
                ("user_id", ASCENDING),
            ]
        )
    )

    created.append(
        db.profile_media.create_index(
            [
                ("user_id", ASCENDING),
                ("position", ASCENDING),
            ]
        )
    )

    created.append(
        db.profile_media.create_index(
            [
                ("user_id", ASCENDING),
                ("status", ASCENDING),
            ]
        )
    )

    created.append(
        db.profile_media.create_index(
            [
                ("storage_key", ASCENDING),
            ],
            unique=True,
        )
    )

    # Prevent the same normalized photo/video from existing
    # more than once as active media for the same user.
    #
    # Deleted history does not participate in this unique
    # constraint, so a previously deleted item may be
    # uploaded again later.
    created.append(
        db.profile_media.create_index(
            [
                ("user_id", ASCENDING),
                ("sha256", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={
                "status": "active",
            },
        )
    )

    # A user may have many non-primary media documents, but
    # at most one active primary item.
    created.append(
        db.profile_media.create_index(
            [
                ("user_id", ASCENDING),
                ("is_primary", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={
                "status": "active",
                "is_primary": True,
            },
        )
    )

    # Video thumbnails use separate objects.
    #
    # Photo documents explicitly store this field as null,
    # so a sparse unique index is not sufficient: MongoDB
    # still indexes an existing null field. Restrict the
    # unique index to real string thumbnail keys only.
    created.append(
        db.profile_media.create_index(
            [
                (
                    "thumbnail_storage_key",
                    ASCENDING,
                ),
            ],
            unique=True,
            partialFilterExpression={
                "thumbnail_storage_key": {
                    "$type": "string",
                },
            },
        )
    )
    # =====================================================
    # MATCHING
    # =====================================================

    created.append(
        db.profile_actions.create_index(
            [
                ("actor_id", ASCENDING),
                ("target_id", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.profile_actions.create_index(
            [
                ("target_id", ASCENDING),
                ("action", ASCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    created.append(
        db.matches.create_index(
            [("pair_key", ASCENDING)],
            unique=True,
        )
    )

    created.append(
        db.matches.create_index(
            [
                ("user_ids", ASCENDING),
                ("status", ASCENDING),
                ("updated_at", DESCENDING),
            ]
        )
    )

    # =====================================================
    # CHAT
    # =====================================================

    created.append(
        db.conversations.create_index(
            [("match_id", ASCENDING)],
            unique=True,
            sparse=True,
        )
    )

    created.append(
        db.conversations.create_index(
            [
                ("member_ids", ASCENDING),
                ("status", ASCENDING),
                ("last_message_at", DESCENDING),
                ("updated_at", DESCENDING),
                ("_id", DESCENDING),
            ]
        )
    )

    created.append(
        db.messages.create_index(
            [
                ("sender_id", ASCENDING),
                ("client_message_id", ASCENDING),
            ],
            unique=True,
        )
    )

    # Older Xuoroni databases may already have the original
    # non-unique conversation/sequence index created under
    # MongoDB's default generated name. Remove only that known
    # legacy definition before creating the stronger unique
    # constraint below.
    message_indexes = (
        db.messages.index_information()
    )

    legacy_sequence_index = (
        message_indexes.get(
            "conversation_id_1_sequence_-1"
        )
    )

    if (
        legacy_sequence_index is not None
        and not legacy_sequence_index.get(
            "unique",
            False,
        )
    ):
        db.messages.drop_index(
            "conversation_id_1_sequence_-1"
        )

    created.append(
        db.messages.create_index(
            [
                ("conversation_id", ASCENDING),
                ("sequence", DESCENDING),
            ],
            unique=True,
            name=(
                "conversation_sequence_unique"
            ),
        )
    )

    # =====================================================
    # SAFETY
    # =====================================================

    created.append(
        db.blocks.create_index(
            [
                ("blocker_id", ASCENDING),
                ("blocked_id", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.reports.create_index(
            [
                ("status", ASCENDING),
                ("severity", DESCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    created.append(
        db.trusted_contacts.create_index(
            [
                ("user_id", ASCENDING),
                ("contact_key", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.location_share_sessions.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
        )
    )

    created.append(
        db.location_share_sessions.create_index(
            [
                ("owner_user_id", ASCENDING),
                ("revoked_at", ASCENDING),
            ]
        )
    )

    created.append(
        db.safe_meets.create_index(
            [
                ("participant_ids", ASCENDING),
                ("status", ASCENDING),
                ("start_at", ASCENDING),
            ]
        )
    )

    # =====================================================
    # CAFE / VENUE / RESERVATIONS
    # =====================================================

    created.append(
        db.venues.create_index(
            [("location", GEOSPHERE)]
        )
    )

    created.append(
        db.venues.create_index(
            [
                ("approval_status", ASCENDING),
                ("city", ASCENDING),
                ("active", ASCENDING),
            ]
        )
    )

    created.append(
        db.venue_slots.create_index(
            [
                ("venue_id", ASCENDING),
                ("start_at", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.reservations.create_index(
            [
                ("venue_id", ASCENDING),
                ("start_at", ASCENDING),
                ("status", ASCENDING),
            ]
        )
    )

    created.append(
        db.reservations.create_index(
            [("idempotency_key", ASCENDING)],
            unique=True,
            sparse=True,
        )
    )

    # =====================================================
    # PHASE 2J: TRANSACTIONAL BOOKING NOTIFICATION OUTBOX
    # =====================================================

    # Exactly one durable outbox record per reservation event.
    # This index must exist before booking event producers are
    # enabled in a live environment.
    created.append(
        db.cafe_booking_outbox.create_index(
            [("event_key", ASCENDING)],
            unique=True,
            name="cafe_booking_outbox_event_key_unique",
        )
    )

    # Supports pending deliveries and retry scheduling.
    # Worker claim/lease logic must be separately implemented.
    created.append(
        db.cafe_booking_outbox.create_index(
            [
                ("delivery_state", ASCENDING),
                ("next_attempt_at", ASCENDING),
                ("locked_until", ASCENDING),
            ],
            name="cafe_booking_outbox_delivery_queue",
        )
    )

    # =====================================================
    # PHASE 2J: CAFE ACCOUNT AND VENUE INDEXES
    # =====================================================

    created.append(
        db.cafe_partner_accounts.create_index(
            [("email", ASCENDING)],
            unique=True,
            name="cafe_partner_email_unique",
        )
    )

    created.append(
        db.cafe_partner_accounts.create_index(
            [
                ("status", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="cafe_partner_moderation_queue",
        )
    )

    created.append(
        db.cafe_partner_accounts.create_index(
            [("venue_id", ASCENDING)],
            unique=True,
            partialFilterExpression={
                "venue_id": {"$type": "objectId"}
            },
            name="cafe_partner_linked_venue_unique",
        )
    )

    created.append(
        db.venues.create_index(
            [
                ("status", ASCENDING),
                ("city", ASCENDING),
                ("owner_id", ASCENDING),
            ],
            name="cafe_venue_status_city_owner",
        )
    )

    created.append(
        db.venues.create_index(
            [
                ("owner_id", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="cafe_venue_owner_created",
        )
    )


    # =====================================================
    # PHASE 2J: CAFE PARTNER SESSION INDEXES
    # =====================================================

    # Every cafe bearer token has a unique digest.
    # Plaintext bearer tokens are never stored.
    created.append(
        db.cafe_partner_sessions.create_index(
            [("token_hash", ASCENDING)],
            unique=True,
            name="cafe_partner_session_token_unique",
        )
    )

    # Expired sessions are removed automatically by MongoDB.
    # Authentication separately checks expiry immediately.
    created.append(
        db.cafe_partner_sessions.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            name="cafe_partner_session_expiry_ttl",
        )
    )

    # Supports a future revoke-all-sessions action for a cafe.
    created.append(
        db.cafe_partner_sessions.create_index(
            [
                ("partner_id", ASCENDING),
                ("revoked_at", ASCENDING),
            ],
            name="cafe_partner_session_owner_revoke",
        )
    )

    # =====================================================
    # CULTURE
    # =====================================================

    created.append(
        db.culture_sources.create_index(
            [("source_url_normalized", ASCENDING)],
            unique=True,
            sparse=True,
        )
    )

    created.append(
        db.culture_entities.create_index(
            [
                ("entity_type", ASCENDING),
                ("slug", ASCENDING),
            ],
            unique=True,
        )
    )

    created.append(
        db.festivals.create_index(
            [
                ("published", ASCENDING),
                ("name_normalized", ASCENDING),
            ]
        )
    )

    # =====================================================
    # NOTIFICATIONS / OPERATIONS
    # =====================================================

    # Exactly one durable in-app Notification Preferences record
    # is allowed per Xuoroni user.
    created.append(
        db.notification_preferences.create_index(
            [
                ("user_id", ASCENDING),
            ],
            unique=True,
            name=(
                "notification_preferences_user_unique"
            ),
        )
    )

    created.append(
        db.notifications.create_index(
            [
                ("user_id", ASCENDING),
                ("read_at", ASCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    # Notification producers may retry after network,
    # worker, or realtime delivery failures. A recipient-
    # scoped optional dedupe key makes those retries
    # atomically idempotent at the durable MongoDB layer.
    #
    # Notifications without a string dedupe key do not
    # participate in this unique constraint.
    created.append(
        db.notifications.create_index(
            [
                ("user_id", ASCENDING),
                ("dedupe_key", ASCENDING),
            ],
            unique=True,
            name=(
                "notification_user_dedupe_unique"
            ),
            partialFilterExpression={
                "dedupe_key": {
                    "$type": "string",
                },
            },
        )
    )

    # System/background reconciliation scans durable Notifications
    # chronologically so missing delivery rows can be repaired after
    # a process dies between Notification creation and provisioning.
    created.append(
        db.notifications.create_index(
            [
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            name=(
                "notification_delivery_reconciliation_scan"
            ),
        )
    )

    # =====================================================
    # NOTIFICATION DELIVERY / BACKGROUND DISPATCH
    # =====================================================

    # One durable supplementary delivery record is allowed
    # per Notification/channel pair.
    created.append(
        db.notification_deliveries.create_index(
            [
                ("notification_id", ASCENDING),
                ("channel", ASCENDING),
            ],
            unique=True,
            name=(
                "notification_delivery_notification_channel_unique"
            ),
        )
    )

    # Workers claim the earliest due pending/retry delivery.
    created.append(
        db.notification_deliveries.create_index(
            [
                ("status", ASCENDING),
                ("next_attempt_at", ASCENDING),
                ("created_at", ASCENDING),
            ],
            name=(
                "notification_delivery_pending_claim"
            ),
        )
    )

    # Expired processing leases can be recovered after
    # worker crash, timeout, or process termination.
    created.append(
        db.notification_deliveries.create_index(
            [
                ("status", ASCENDING),
                ("lease_expires_at", ASCENDING),
            ],
            name=(
                "notification_delivery_lease_recovery"
            ),
        )
    )

    created.append(
        db.audit_logs.create_index(
            [
                ("actor_id", ASCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    created.append(
        db.audit_logs.create_index(
            [
                ("resource_type", ASCENDING),
                ("resource_id", ASCENDING),
                ("created_at", DESCENDING),
            ]
        )
    )

    return created
