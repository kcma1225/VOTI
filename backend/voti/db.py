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


def init_db() -> None:
    """Create extensions and tables. Idempotent; called by the API on startup and by `voti init`."""
    from . import models  # noqa: F401  (register tables)

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('voti.init_db'))"))
        models.Base.metadata.create_all(conn)
