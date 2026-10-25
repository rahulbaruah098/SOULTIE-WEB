
import os


def apply_production_security(app):
    # Only the explicit production mode activates this gate.
    if os.environ.get("XUORONI_DEPLOYMENT_MODE") != "production":
        return False

    if app.debug or app.config.get("DEBUG") is True:
        raise RuntimeError(
            "Production startup refused: Flask debug is enabled."
        )

    secret = app.config.get("SECRET_KEY")
    if not isinstance(secret, (str, bytes)) or len(secret) < 32:
        raise RuntimeError(
            "Production startup refused: application secret is weak."
        )

    if secret in (
        "dev", "development", "changeme",
        "replace-this-with-a-secure-secret",
    ):
        raise RuntimeError(
            "Production startup refused: placeholder secret."
        )

    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PREFERRED_URL_SCHEME"] = "https"

    return True
