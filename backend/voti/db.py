"""Database engine and session helpers (SQLAlchemy 2.0, psycopg 3)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_config


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_config().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def _factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction per block: commit on success, rollback on error."""
    s = _factory()()
    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


# Lightweight migrations for databases created before a column/index existed. create_all() never alters an
# existing table, so every column added to a model after the first deployment is listed here too. Each
# statement must be idempotent (IF NOT EXISTS) and cheap (no table rewrite: nullable columns without default).
MIGRATIONS: tuple[str, ...] = (
    # 2026-10-27: the single "fetch" queue was split so channel scans, caption details and downloads run in
    # parallel and one huge channel can't block the rest.
    "UPDATE jobs SET queue = 'scan' WHERE queue = 'fetch' AND kind IN ('source.resolve', 'channel.list', 'playlist.list')",
    "UPDATE jobs SET queue = 'detail' WHERE queue = 'fetch' AND kind = 'media.detail'",
    "UPDATE jobs SET queue = 'download' WHERE queue = 'fetch' AND kind = 'media.download'",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS run_after TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS jobs_running_heartbeat ON jobs (heartbeat_at) WHERE status = 'running'",
    "ALTER TABLE media ADD COLUMN IF NOT EXISTS discovered_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS media_discovered ON media (discovered_at) WHERE discovered_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS jobs_channel_kind_active ON jobs (channel_id, kind) "
    "WHERE status IN ('queued','running')",
)


def init_db() -> None:
    """Create extensions and tables, then apply MIGRATIONS. Idempotent; called by the API on startup,
    by every worker on startup and by `voti init`."""
    from . import models  # noqa: F401  (register tables)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('voti.init_db'))"))
        models.Base.metadata.create_all(conn)
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
