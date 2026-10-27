"""PostgreSQL access. The schema lives in db/init.sql only (applied by the database container on first start).

Connections are short-lived and autocommit; multi-statement writes use `transaction()`. Call sites keep the
`?` placeholder style and read rows by name or position, so they stay independent of the driver."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Row(dict):
    """Result row readable by column name or by position."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


class Connection:
    def __init__(self, raw: psycopg.Connection):
        self.raw = raw

    def execute(self, sql: str, params=()) -> psycopg.Cursor:
        if params:
            sql = sql.replace("%", "%%").replace("?", "%s")
        return self.raw.execute(sql, params or None)

    def close(self) -> None:
        self.raw.close()


_checked: set[str] = set()


def init_db(dsn: str) -> None:
    """Fail early and clearly when the database has no schema yet. It never creates or alters tables."""
    if dsn in _checked:
        return
    with connect(dsn) as db:
        if db.execute("SELECT to_regclass('sources')").fetchone()[0] is None:
            raise RuntimeError("資料庫尚無資料表；請以 db/init.sql 初始化（docker compose up -d db 會自動執行）")
    _checked.add(dsn)


@contextmanager
def connect(dsn: str):
    raw = psycopg.connect(dsn, autocommit=True, row_factory=_row_factory, connect_timeout=10)
    try:
        yield Connection(raw)
    finally:
        raw.close()


@contextmanager
def transaction(db: Connection):
    with db.raw.transaction():
        yield db


def rows(db: Connection, sql: str, values: tuple = ()) -> list[dict]:
    return [dict(r) for r in db.execute(sql, values).fetchall()]
