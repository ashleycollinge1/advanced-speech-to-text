import os
import tempfile

import pytest

# Configure before the app is imported.
_media = tempfile.mkdtemp(prefix="stt-media-")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://stt:stt@localhost:55432/stt")
os.environ["MEDIA_DIR"] = _media
os.environ["MODELS_DIR"] = tempfile.mkdtemp(prefix="stt-models-")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "correct-horse-battery"
os.environ["COOKIE_SECURE"] = "false"
os.environ["MAX_UPLOAD_MB"] = "1"


def _db_available() -> bool:
    from sqlalchemy import create_engine, text

    try:
        with create_engine(os.environ["DATABASE_URL"]).connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def migrated():
    if not _db_available():
        pytest.skip("PostgreSQL not available (set DATABASE_URL)")
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


def mark_models_ready(names=None):
    """Simulate a worker that already has these models on disk (default: all configured)."""
    from app.config import settings
    from app.db import session_scope
    from app.models import MODEL_READY, WhisperModelState

    with session_scope() as db:
        for name in names or settings.whisper_models:
            row = db.get(WhisperModelState, name) or WhisperModelState(name=name)
            row.status, row.bytes_done, row.bytes_total, row.error = MODEL_READY, 1000, 1000, None
            db.add(row)


@pytest.fixture()
def client(migrated):
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import engine
    from app.main import app
    from app.security import login_limiter

    with engine.begin() as c:
        c.execute(
            text("TRUNCATE users, sessions, recordings, segments, highlights, whisper_models RESTART IDENTITY CASCADE")
        )
    mark_models_ready()
    login_limiter.reset()
    with TestClient(app) as tc:
        yield tc


class Api:
    """Small helper that tracks the CSRF token like the browser client does."""

    def __init__(self, client):
        self.c = client
        self.csrf = None

    def login(self, username="admin", password="correct-horse-battery"):
        r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r

    def _h(self):
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers={**self._h(), **kw.pop("headers", {})}, **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self._h(), **kw)


@pytest.fixture()
def api(client):
    a = Api(client)
    assert a.login().status_code == 200
    return a
