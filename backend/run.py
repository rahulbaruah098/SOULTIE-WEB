"""Soultie backend runtime entry point."""

import os
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import mongo, socketio


def ensure_default_admin(app):
    """
    Create the configured Soultie admin only when it does not
    already exist.

    Existing admin passwords are never silently overwritten.
    """
    email = str(
        os.getenv(
            "ADMIN_EMAIL",
            "",
        )
    ).strip().lower()

    password = str(
        os.getenv(
            "ADMIN_PASSWORD",
            "",
        )
    )

    if not email or not password:
        app.logger.warning(
            "Default admin bootstrap skipped: "
            "ADMIN_EMAIL or ADMIN_PASSWORD is missing."
        )
        return

    with app.app_context():
        existing = mongo.db.admins.find_one(
            {
                "email": email,
            }
        )

        if existing is not None:
            return

        now = datetime.now(
            timezone.utc
        ).isoformat()

        mongo.db.admins.insert_one(
            {
                "email": email,
                "password_hash": (
                    generate_password_hash(
                        password
                    )
                ),
                "created_at": now,
                "updated_at": now,
            }
        )

        app.logger.info(
            "Default SOULTIE admin created."
        )


app = create_app()

ensure_default_admin(
    app
)


if __name__ == "__main__":
    host = os.getenv(
        "HOST",
        "0.0.0.0",
    )

    port = int(
        os.getenv(
            "PORT",
            "5000",
        )
    )

    socketio.run(
        app,
        host=host,
        port=port,
        debug=bool(
            app.config.get(
                "DEBUG",
                False,
            )
        ),
        use_reloader=False,
    )
