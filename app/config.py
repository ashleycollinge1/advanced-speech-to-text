"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "stt")
    password = os.environ.get("POSTGRES_PASSWORD", "stt")
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "stt")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=_database_url)
    media_dir: Path = field(default_factory=lambda: Path(os.environ.get("MEDIA_DIR", "/data/media")))
    models_dir: Path = field(default_factory=lambda: Path(os.environ.get("MODELS_DIR", "/data/models")))
    frontend_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("FRONTEND_DIR", str(Path(__file__).resolve().parent.parent / "frontend"))
        )
    )

    admin_username: str = field(default_factory=lambda: os.environ.get("ADMIN_USERNAME", "admin"))
    admin_password: str = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD", ""))

    # Set to false only when serving over plain HTTP (e.g. local dev).
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", True))
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 14))
    min_password_length: int = field(default_factory=lambda: _int("MIN_PASSWORD_LENGTH", 12))
    login_max_failures: int = field(default_factory=lambda: _int("LOGIN_MAX_FAILURES", 5))
    login_window_seconds: int = field(default_factory=lambda: _int("LOGIN_WINDOW_SECONDS", 900))

    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 2048))

    # Default model for new uploads, and the list users may choose from.
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", "large-v3-turbo"))
    whisper_models_raw: str = field(
        default_factory=lambda: os.environ.get("WHISPER_MODELS", "small.en,medium.en,large-v3-turbo,large-v3")
    )
    whisper_compute_type: str = field(default_factory=lambda: os.environ.get("WHISPER_COMPUTE_TYPE", "int8"))
    whisper_threads: int = field(default_factory=lambda: _int("WHISPER_THREADS", 0))  # 0 = auto
    whisper_beam_size: int = field(default_factory=lambda: _int("WHISPER_BEAM_SIZE", 5))
    worker_poll_seconds: int = field(default_factory=lambda: _int("WORKER_POLL_SECONDS", 3))
    worker_stale_minutes: int = field(default_factory=lambda: _int("WORKER_STALE_MINUTES", 15))
    max_attempts: int = field(default_factory=lambda: _int("MAX_ATTEMPTS", 3))

    app_version: str = field(default_factory=lambda: os.environ.get("APP_VERSION", "dev"))

    @property
    def whisper_models(self) -> list[str]:
        """Allowed models, in the configured order; the default is always included."""
        models = [m.strip() for m in self.whisper_models_raw.split(",") if m.strip()]
        if self.whisper_model not in models:
            models.insert(0, self.whisper_model)
        return list(dict.fromkeys(models))


settings = Settings()
