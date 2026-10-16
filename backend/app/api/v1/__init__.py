from flask import Blueprint

from app.api.v1.auth.routes import (
    auth_bp,
)
from app.api.v1.culture.routes import (
    culture_bp,
)
from app.api.v1.discovery.routes import (
    discovery_bp,
)
from app.api.v1.matches.routes import (
    matches_bp,
)
from app.api.v1.media.routes import (
    media_bp,
)
from app.api.v1.onboarding.routes import (
    onboarding_bp,
)
from app.api.v1.profiles.routes import (
    profiles_bp,
)


from app.api.v1.compatibility.routes import (
    compatibility_bp,
)


from app.api.v1.chats.routes import (
    chats_bp,
)


from .notifications.routes import notifications_bp
from .push_devices import push_devices_bp

api_v1_bp = Blueprint(
    "api_v1",
    __name__,
    url_prefix="/api/v1",
)

api_v1_bp.register_blueprint(
    auth_bp,
    url_prefix="/auth",
)

api_v1_bp.register_blueprint(
    profiles_bp
)

api_v1_bp.register_blueprint(
    onboarding_bp
)

api_v1_bp.register_blueprint(
    culture_bp
)

api_v1_bp.register_blueprint(
    discovery_bp
)

api_v1_bp.register_blueprint(
    matches_bp
)

api_v1_bp.register_blueprint(
    media_bp
)


api_v1_bp.register_blueprint(
    compatibility_bp
)


api_v1_bp.register_blueprint(
    chats_bp
)


api_v1_bp.register_blueprint(
    notifications_bp
)

api_v1_bp.register_blueprint(
    push_devices_bp
)

__all__ = [
    "api_v1_bp",
]
