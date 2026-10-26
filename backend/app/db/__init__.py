from flask import Flask

from app.db.indexes import ensure_indexes
from app.extensions import mongo


def initialize_database(app: Flask) -> None:
    """
    Initialize Soultie MongoDB infrastructure.

    Index creation is idempotent and can safely run again
    when the configured definitions have not changed.
    """

    if not app.config.get(
        "AUTO_ENSURE_INDEXES",
        True,
    ):
        app.logger.info(
            "Automatic MongoDB index initialization disabled"
        )
        return

    try:
        indexes = ensure_indexes(
            mongo.db
        )

        app.logger.info(
            "MongoDB indexes initialized: %s",
            len(indexes),
        )

    except Exception:
        app.logger.exception(
            "MongoDB index initialization failed"
        )
        raise


__all__ = [
    "initialize_database",
]
