import os


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class BaseConfig:
    APP_NAME = "Xuoroni"

    API_VERSION = os.getenv(
        "API_VERSION",
        "v1",
    )

    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "dev-only-change-me",
    )

    MONGO_URI = os.getenv(
        "MONGO_URI",
        "mongodb://localhost:27017/xuoroni",
    )

    REDIS_URL = os.getenv(
        "REDIS_URL",
        "redis://localhost:6379/0",
    )

    FRONTEND_ORIGIN = os.getenv(
        "FRONTEND_ORIGIN",
        "http://localhost:5173",
    )

    SOCKETIO_MESSAGE_QUEUE = os.getenv(
        "SOCKETIO_MESSAGE_QUEUE",
        "",
    )

    # =====================================================
    # REALTIME CHAT EPHEMERAL STATE
    # =====================================================
    #
    # Typing state is intentionally ephemeral and lives only
    # in Redis. It must never be treated as durable Chat data.
    REALTIME_TYPING_KEY_PREFIX = os.getenv(
        "REALTIME_TYPING_KEY_PREFIX",
        "xuoroni:realtime:typing",
    ).strip()

    REALTIME_TYPING_TTL_SECONDS = int(
        os.getenv(
            "REALTIME_TYPING_TTL_SECONDS",
            "8",
        )
    )

    # Presence also lives only in Redis. Each authenticated
    # Socket.IO connection will own its own short-lived key,
    # allowing multi-device presence without durable state.
    REALTIME_PRESENCE_KEY_PREFIX = os.getenv(
        "REALTIME_PRESENCE_KEY_PREFIX",
        "xuoroni:realtime:presence",
    ).strip()

    REALTIME_PRESENCE_TTL_SECONDS = int(
        os.getenv(
            "REALTIME_PRESENCE_TTL_SECONDS",
            "75",
        )
    )

    REALTIME_PRESENCE_HEARTBEAT_SECONDS = int(
        os.getenv(
            "REALTIME_PRESENCE_HEARTBEAT_SECONDS",
            "25",
        )
    )

    CELERY_BROKER_URL = os.getenv(
        "CELERY_BROKER_URL",
        "redis://localhost:6379/1",
    )

    CELERY_RESULT_BACKEND = os.getenv(
        "CELERY_RESULT_BACKEND",
        "redis://localhost:6379/2",
    )

    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY",
        "dev-only-jwt-secret-change-me",
    )

    JWT_ACCESS_TOKEN_MINUTES = int(
        os.getenv(
            "JWT_ACCESS_TOKEN_MINUTES",
            "15",
        )
    )

    JWT_REFRESH_TOKEN_DAYS = int(
        os.getenv(
            "JWT_REFRESH_TOKEN_DAYS",
            "30",
        )
    )

    JWT_ALGORITHM = "HS256"

    JWT_ISSUER = os.getenv(
        "JWT_ISSUER",
        "xuoroni-backend",
    )

    JWT_AUDIENCE = os.getenv(
        "JWT_AUDIENCE",
        "xuoroni-mobile",
    )

    GOOGLE_CLIENT_ID = os.getenv(
        "GOOGLE_CLIENT_ID",
        "",
    )

    # SMTP / Email Authentication
    SMTP_HOST = os.getenv(
        "SMTP_HOST",
        "",
    )

    SMTP_PORT = int(
        os.getenv(
            "SMTP_PORT",
            "587",
        )
    )

    SMTP_USERNAME = os.getenv(
        "SMTP_USERNAME",
        "",
    )

    SMTP_PASSWORD = os.getenv(
        "SMTP_PASSWORD",
        "",
    )

    SMTP_FROM_EMAIL = os.getenv(
        "SMTP_FROM_EMAIL",
        "",
    )

    SMTP_FROM_NAME = os.getenv(
        "SMTP_FROM_NAME",
        "Xuoroni",
    )

    SMTP_USE_TLS = (
        os.getenv(
            "SMTP_USE_TLS",
            "true",
        ).strip().lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )

    SMTP_TIMEOUT_SECONDS = int(
        os.getenv(
            "SMTP_TIMEOUT_SECONDS",
            "15",
        )
    )

    # Email OTP uses its own Redis namespace so it
    # can never collide with mobile OTP records.
    EMAIL_OTP_KEY_PREFIX = os.getenv(
        "EMAIL_OTP_KEY_PREFIX",
        "xuoroni:email-otp",
    )

    # Development only. Never expose this in production.
    DEV_EMAIL_OTP_CODE = os.getenv(
        "DEV_EMAIL_OTP_CODE",
        "",
    )

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False

    JSON_SORT_KEYS = False

    MAX_CONTENT_LENGTH = int(
        os.getenv(
            "MAX_CONTENT_LENGTH",
            str(20 * 1024 * 1024),
        )
    )

    # =====================================================
    # PROFILE MEDIA
    # =====================================================
    #
    # The six-item limit is shared across photos and videos.
    # Example: 4 photos + 2 videos = 6 profile media items.
    #
    # Local storage is used during development. The media
    # service depends on a storage abstraction so S3 can be
    # added later without changing media business logic.
    MEDIA_STORAGE_BACKEND = os.getenv(
        "MEDIA_STORAGE_BACKEND",
        "local",
    ).strip().lower()

    MEDIA_LOCAL_ROOT = os.getenv(
        "MEDIA_LOCAL_ROOT",
        "storage/profile_media",
    )

    MEDIA_MAX_PROFILE_ITEMS = int(
        os.getenv(
            "MEDIA_MAX_PROFILE_ITEMS",
            "6",
        )
    )

    # Raw upload limit before normalization.
    #
    # Flask's global MAX_CONTENT_LENGTH remains the absolute
    # request ceiling. This media-specific limit is enforced
    # by the media processing service.
    MEDIA_MAX_UPLOAD_BYTES = int(
        os.getenv(
            "MEDIA_MAX_UPLOAD_BYTES",
            str(10 * 1024 * 1024),
        )
    )

    # Image normalization.
    MEDIA_MAX_WIDTH = int(
        os.getenv(
            "MEDIA_MAX_WIDTH",
            "2000",
        )
    )

    MEDIA_MAX_HEIGHT = int(
        os.getenv(
            "MEDIA_MAX_HEIGHT",
            "2000",
        )
    )

    MEDIA_MAX_IMAGE_PIXELS = int(
        os.getenv(
            "MEDIA_MAX_IMAGE_PIXELS",
            "40000000",
        )
    )

    MEDIA_OUTPUT_FORMAT = os.getenv(
        "MEDIA_OUTPUT_FORMAT",
        "WEBP",
    ).strip().upper()

    MEDIA_WEBP_QUALITY = int(
        os.getenv(
            "MEDIA_WEBP_QUALITY",
            "85",
        )
    )

    # Short profile videos.
    #
    # Xuoroni V1 supports clips up to four seconds.
    MEDIA_VIDEO_MAX_DURATION_MS = int(
        os.getenv(
            "MEDIA_VIDEO_MAX_DURATION_MS",
            "4000",
        )
    )

    MEDIA_VIDEO_MAX_WIDTH = int(
        os.getenv(
            "MEDIA_VIDEO_MAX_WIDTH",
            "1080",
        )
    )

    MEDIA_VIDEO_MAX_HEIGHT = int(
        os.getenv(
            "MEDIA_VIDEO_MAX_HEIGHT",
            "1920",
        )
    )

    MEDIA_VIDEO_OUTPUT_FORMAT = os.getenv(
        "MEDIA_VIDEO_OUTPUT_FORMAT",
        "mp4",
    ).strip().lower()

    MEDIA_VIDEO_CODEC = os.getenv(
        "MEDIA_VIDEO_CODEC",
        "libx264",
    ).strip()

    MEDIA_VIDEO_AUDIO_CODEC = os.getenv(
        "MEDIA_VIDEO_AUDIO_CODEC",
        "aac",
    ).strip()

    MEDIA_VIDEO_CRF = int(
        os.getenv(
            "MEDIA_VIDEO_CRF",
            "23",
        )
    )

    MEDIA_VIDEO_PRESET = os.getenv(
        "MEDIA_VIDEO_PRESET",
        "medium",
    ).strip()

    MEDIA_VIDEO_THUMBNAIL_QUALITY = int(
        os.getenv(
            "MEDIA_VIDEO_THUMBNAIL_QUALITY",
            "85",
        )
    )

    MEDIA_FFMPEG_BINARY = os.getenv(
        "MEDIA_FFMPEG_BINARY",
        "ffmpeg",
    ).strip()

    MEDIA_FFPROBE_BINARY = os.getenv(
        "MEDIA_FFPROBE_BINARY",
        "ffprobe",
    ).strip()

    REQUEST_ID_HEADER = "X-Request-ID"

    OTP_LENGTH = int(
        os.getenv(
            "OTP_LENGTH",
            "6",
        )
    )

    OTP_TTL_SECONDS = int(
        os.getenv(
            "OTP_TTL_SECONDS",
            "300",
        )
    )

    OTP_RESEND_COOLDOWN_SECONDS = int(
        os.getenv(
            "OTP_RESEND_COOLDOWN_SECONDS",
            "60",
        )
    )

    OTP_MAX_ATTEMPTS = int(
        os.getenv(
            "OTP_MAX_ATTEMPTS",
            "5",
        )
    )

    OTP_REQUEST_WINDOW_SECONDS = int(
        os.getenv(
            "OTP_REQUEST_WINDOW_SECONDS",
            "3600",
        )
    )

    OTP_MAX_REQUESTS_PER_WINDOW = int(
        os.getenv(
            "OTP_MAX_REQUESTS_PER_WINDOW",
            "5",
        )
    )

    OTP_KEY_PREFIX = os.getenv(
        "OTP_KEY_PREFIX",
        "xuoroni:otp",
    )

    OTP_HASH_SECRET = os.getenv(
        "OTP_HASH_SECRET",
        JWT_SECRET_KEY,
    )

    DEV_OTP_CODE = os.getenv(
        "DEV_OTP_CODE",
        "",
    )

    AUTO_ENSURE_INDEXES = _env_bool(
        "AUTO_ENSURE_INDEXES",
        True,
    )


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TESTING = False

    SESSION_COOKIE_SECURE = False

    EXPOSE_DEV_OTP = _env_bool(
        "EXPOSE_DEV_OTP",
        True,
    )


class TestingConfig(BaseConfig):
    DEBUG = False
    TESTING = True

    SESSION_COOKIE_SECURE = False

    EXPOSE_DEV_OTP = True
    AUTO_ENSURE_INDEXES = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False

    SESSION_COOKIE_SECURE = True

    EXPOSE_DEV_OTP = False


def get_config():
    environment = os.getenv(
        "APP_ENV",
        os.getenv(
            "FLASK_ENV",
            "development",
        ),
    ).strip().lower()

    if environment == "production":
        return ProductionConfig

    if environment == "testing":
        return TestingConfig

    return DevelopmentConfig
