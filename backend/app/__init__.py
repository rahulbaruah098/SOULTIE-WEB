"""Xuoroni shared Flask backend."""

from pathlib import Path

from dotenv import load_dotenv
from flask import Flask


# Load backend/.env BEFORE importing configuration.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")


from .api.system import system_bp
from .api.legacy import legacy_api_bp
from .api.v1 import api_v1_bp
from .config import get_config
from .db import initialize_database
from .errors import register_error_handlers
from .extensions import init_extensions
from .logging import configure_logging
from .middleware import register_middleware

# Import realtime handlers once so their Socket.IO decorators
# are registered on the shared extension before normal app startup.
from . import realtime as _realtime


def create_app(config_override=None) -> Flask:
    app = Flask(__name__)

    app.config.from_object(
        get_config()
    )

    if config_override:
        app.config.update(
            config_override
        )

    configure_logging(app)
    init_extensions(app)

    register_middleware(app)
    register_error_handlers(app)

    app.register_blueprint(
        system_bp
    )

    app.register_blueprint(
        legacy_api_bp
    )

    app.register_blueprint(
        api_v1_bp
    )

    with app.app_context():
        initialize_database(app)

    app.logger.info(
        "Xuoroni application initialized",
        extra={
            "app_name": app.config.get(
                "APP_NAME"
            ),
            "api_version": app.config.get(
                "API_VERSION"
            ),
        },
    )

    # PHASE_2N_OUTBOX_STARTUP_INDEXES
    with app.app_context():
        from app.extensions import mongo as _sm_outbox_mongo
        from app.repositories.safe_meet_notification_outbox_indexes import (
            ensure_safe_meet_outbox_indexes as _sm_ensure_outbox_indexes,
        )
        _sm_ensure_outbox_indexes(_sm_outbox_mongo.db)

    # SAFE_MEET_REMINDER_INDEX_STARTUP_R5
    with app.app_context():
        from app.extensions import mongo as _reminder_mongo
        from app.repositories.safe_meet_reminder_indexes import ensure_safe_meet_reminder_indexes
        ensure_safe_meet_reminder_indexes(_reminder_mongo.db)

    # SAFE_MEET_CHECKIN_INDEX_STARTUP_CP14B
    with app.app_context():
        from app.extensions import mongo as _checkin_mongo
        from app.services.safe_meet_checkin_service import (
            ensure_safe_meet_checkin_indexes,
        )
        ensure_safe_meet_checkin_indexes(_checkin_mongo.db)

    # SAFE_MEET_SOS_INDEX_STARTUP_CP16B
    with app.app_context():
        from app.extensions import mongo
        from app.services.safe_meet_sos_service import ensure_safe_meet_sos_indexes
        ensure_safe_meet_sos_indexes(mongo.db)
    # SAFE_MEET_PRODUCTION_SECURITY_CP18G
    from app.services.safe_meet_production_security import apply_production_security
    apply_production_security(app)
    return app


__all__ = [
    "create_app",
]

