from app.api.v1.cafe_booking_action_routes import cafe_booking_action_bp
from app.api.v1.cafe_booking_list_routes import cafe_booking_list_bp
from app.api.v1.cafe_email_verification_routes import cafe_email_verification_bp
from flask import Blueprint
from app.api.v1.cafe_registration_routes import cafe_registration_bp
from app.api.v1.cafe_admin_routes import cafe_admin_bp
from app.api.v1.cafe_auth_routes import cafe_auth_bp
from app.api.v1.cafe_venue_routes import cafe_venue_bp
from app.api.v1.cafe_venue_operations_routes import cafe_venue_operations_bp
from app.api.v1.cafe_booking_configuration_routes import cafe_booking_configuration_bp

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

api_v1_bp.register_blueprint(
    cafe_admin_bp
)

api_v1_bp.register_blueprint(
    cafe_registration_bp
)

api_v1_bp.register_blueprint(
    cafe_auth_bp
)
api_v1_bp.register_blueprint(cafe_venue_bp)
api_v1_bp.register_blueprint(cafe_venue_operations_bp)
api_v1_bp.register_blueprint(cafe_booking_configuration_bp)
api_v1_bp.register_blueprint(cafe_booking_action_bp)
api_v1_bp.register_blueprint(cafe_booking_list_bp)

api_v1_bp.register_blueprint(
    cafe_email_verification_bp
)

__all__ = [
    "api_v1_bp",
]
