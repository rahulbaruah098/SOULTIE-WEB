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

    return app


__all__ = [
    "create_app",
]

