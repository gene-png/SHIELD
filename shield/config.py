"""Application configuration. All settings come from environment variables."""
import os
import tempfile
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


class Config:
    # Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    PREFERRED_URL_SCHEME = "https" if not _bool("FLASK_DEBUG") else "http"

    # SQLAlchemy
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://shield:shield_dev_password@db:5432/shield",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = not _bool("FLASK_DEBUG")
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # CSRF
    WTF_CSRF_TIME_LIMIT = 3600

    # Rate limit
    RATELIMIT_DEFAULT = "200 per minute"
    RATELIMIT_STORAGE_URI = "memory://"

    # Anthropic
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL_APP = os.environ.get("ANTHROPIC_MODEL_APP", "claude-opus-4-7")
    # 16384 is the practical operating point: large enough to fit
    # ~100 of 222 P3 ATT&CK findings with rationale, small enough to
    # finish well inside the 600s RQ job timeout (32000 took ~10 min
    # and got killed by the death penalty). The truncation-aware JSON
    # parser in shield/tasks.py recovers whatever findings did fit and
    # synthesizes the summary counts from them, so a partial response
    # is still actionable.
    ANTHROPIC_MAX_OUTPUT_TOKENS = int(os.environ.get("ANTHROPIC_MAX_OUTPUT_TOKENS", "16384"))
    AI_MODE = os.environ.get("AI_MODE", "real")  # "real" or "fixture"

    # Keycloak (OIDC)
    KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
    KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "shield-dev")
    KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "shield-app")
    KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

    # File storage (dev: local volume; prod: object storage)
    ARTIFACT_STORAGE_DIR = os.environ.get("ARTIFACT_STORAGE_DIR", "/app/artifacts")
    MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 MB upload cap


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    AI_MODE = "fixture"
    # CI runs on the bare GitHub Actions runner; the default `/app`
    # directory doesn't exist and the runner can't create it. Point
    # artifact storage at a tempdir-rooted path so the spine writers
    # can mkdir under it and write files.
    ARTIFACT_STORAGE_DIR = os.environ.get(
        "ARTIFACT_STORAGE_DIR",
        os.path.join(tempfile.gettempdir(), "shield-test-artifacts"),
    )
