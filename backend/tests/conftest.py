"""Shared pytest fixtures.

Isolation (override with env vars when running several suites concurrently):
  VOTI_TEST_DB      database name (default voti_test_api), created on demand on the DATABASE_URL server
  VOTI_TEST_BUCKET  S3 bucket (default voti-test-api)
  VOTI_TEST_REDIS_DB  redis db index (default 9)

Fixtures
  db_engine (session)  fresh schema: public schema dropped and recreated, then voti.db.init_db()
  db                   clean tables (TRUNCATE) + a Session (committed objects are visible to the app)
  bucket               the test bucket exists
  app / client         FastAPI app without lifespan + TestClient (Host/Origin = http://localhost)
  make_user            factory(username, role="annotator", password=...) -> User
  admin_client / annotator_client   logged-in TestClients
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

# ── environment: must be set before voti.config.get_config() is first called ──
_base_db = os.environ.get("DATABASE_URL", "postgresql+psycopg://voti:voti@127.0.0.1:55432/voti")
TEST_DB = os.environ.get("VOTI_TEST_DB", "voti_test_api")
_parts = urlsplit(_base_db)
os.environ["DATABASE_URL"] = urlunsplit(_parts._replace(path=f"/{TEST_DB}"))
_ADMIN_DB_URL = urlunsplit(_parts._replace(path="/postgres"))

_base_redis = os.environ.get("REDIS_URL", "redis://127.0.0.1:56379/0")
_rp = urlsplit(_base_redis)
os.environ["REDIS_URL"] = urlunsplit(_rp._replace(path=f"/{os.environ.get('VOTI_TEST_REDIS_DB', '9')}"))

os.environ.setdefault("S3_ENDPOINT", "http://127.0.0.1:59000")
os.environ.setdefault("S3_ACCESS_KEY", "voti")
os.environ.setdefault("S3_SECRET_KEY", "voti-secret-change-me")
os.environ["S3_BUCKET"] = os.environ.get("VOTI_TEST_BUCKET", "voti-test-api")
os.environ["ADMIN_PASSWORD"] = ""
os.environ["ADMIN_USERNAME"] = "admin"
os.environ.setdefault("PUBLIC_HOSTS", "voti.example.test")
os.environ.setdefault("WORK_DIR", os.path.join(os.environ.get("TMPDIR", "/tmp"), "voti-test-work"))

TEST_PASSWORD = "correct horse battery"
ORIGIN = "http://localhost"


def _ensure_database() -> None:
    import psycopg

    dsn = _ADMIN_DB_URL.replace("postgresql+psycopg://", "postgresql://")
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.OperationalError:  # no access to the maintenance db: fall back to the base db
        conn = psycopg.connect(_base_db.replace("postgresql+psycopg://", "postgresql://"), autocommit=True)
    with conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')


@pytest.fixture(scope="session")
def db_engine():
    from sqlalchemy import text

    from voti.config import get_config
    from voti.db import get_engine, init_db

    get_config.cache_clear()
    assert get_config().database_url.endswith(f"/{TEST_DB}"), "refusing to run tests against a non-test database"
    _ensure_database()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    init_db()
    return engine


@pytest.fixture(scope="session")
def bucket():
    from voti import storage

    storage.ensure_bucket()
    return storage.bucket()


def _truncate(engine) -> None:  # noqa: ANN001
    from sqlalchemy import text

    from voti.models import Base

    names = ", ".join(f'"{t.name}"' for t in Base.metadata.tables.values())
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(db_engine, bucket):  # noqa: ANN001
    from voti.db import _factory
    from voti.queue import redis_client

    _truncate(db_engine)
    try:
        redis_client().flushdb()
    except Exception:  # noqa: BLE001
        pass
    s = _factory()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def app(db):  # noqa: ANN001
    from voti.api.app import create_app

    return create_app(run_startup=False)


@pytest.fixture
def client(app):  # noqa: ANN001
    from fastapi.testclient import TestClient

    with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def make_user(db):  # noqa: ANN001
    from voti.auth import hash_password
    from voti.models import User

    def make(username: str, role: str = "annotator", password: str = TEST_PASSWORD, active: bool = True) -> User:
        u = User(username=username, display_name=username, password_hash=hash_password(password), role=role,
                 is_active=active)
        db.add(u)
        db.commit()
        return u
    return make


def _login(app, username: str, password: str = TEST_PASSWORD):  # noqa: ANN001, ANN202
    from fastapi.testclient import TestClient

    c = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin_user(make_user):  # noqa: ANN001
    return make_user("admin", role="admin")


@pytest.fixture
def admin_client(app, admin_user):  # noqa: ANN001
    with _login(app, "admin") as c:
        yield c


@pytest.fixture
def annotator_client(app, make_user):  # noqa: ANN001
    make_user("anno", role="annotator")
    with _login(app, "anno") as c:
        yield c


class Factory:
    """Small row builders for tests. Every method commits."""

    def __init__(self, s):  # noqa: ANN001
        self.s = s
        self._n = 0

    def _next(self) -> int:
        self._n += 1
        return self._n

    def add(self, obj):  # noqa: ANN001, ANN201
        self.s.add(obj)
        self.s.commit()
        return obj

    def channel(self, **kw):  # noqa: ANN201
        from voti.models import Channel
        n = self._next()
        kw.setdefault("platform_channel_id", f"UC{n:022d}")
        kw.setdefault("title", f"channel {n}")
        kw.setdefault("url", f"https://www.youtube.com/channel/{kw['platform_channel_id']}")
        return self.add(Channel(**kw))

    def media(self, **kw):  # noqa: ANN201
        from voti.models import Media
        n = self._next()
        kw.setdefault("platform_media_id", f"vid{n:08d}")
        kw.setdefault("title", f"video {n}")
        kw.setdefault("url", f"https://www.youtube.com/watch?v={kw['platform_media_id']}")
        kw.setdefault("duration_sec", 600.0)
        return self.add(Media(**kw))

    def segment(self, media, start: float, end: float, **kw):  # noqa: ANN001, ANN201
        from voti.models import Segment
        kw.setdefault("original_text", f"text {self._next()}")
        kw.setdefault("text_source", "manual_caption")
        return self.add(Segment(media_id=media.id, start_sec=start, end_sec=end, **kw))


@pytest.fixture
def factory(db):  # noqa: ANN001
    return Factory(db)


# Caption verification (media.detail / voti.sources.youtube.verify_captions) must never reach YouTube from tests:
# by default every fetched caption track contains speech. Tests that need empty / failing tracks override
# `voti.sources.youtube.fetch_caption_bytes` themselves.
SPEECH_JSON3 = b'{"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "\xe5\xa4\xa7\xe5\xae\xb6\xe5\xa5\xbd"}]}]}'


@pytest.fixture(autouse=True)
def _no_caption_network(monkeypatch):  # noqa: ANN001
    from voti.sources import youtube as yt

    monkeypatch.setattr(yt, "fetch_caption_bytes", lambda fmt, fo, headers=None: SPEECH_JSON3)
    # the API's inline channel lookup must not reach YouTube: behave as if it failed (→ background job path)
    from voti.api import sources as api_sources
    monkeypatch.setattr(api_sources, "_resolve_channel_now", lambda target: None)
