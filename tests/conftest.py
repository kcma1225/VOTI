"""Each test gets its own Postgres schema built from db/init.sql, so tests never see each other's rows."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

INIT_SQL = Path(__file__).resolve().parents[1] / "db" / "init.sql"
BASE_URL = os.environ.get("CORPUS_TEST_DATABASE_URL", "postgresql://corpus:corpus@127.0.0.1:55432/corpus")


@pytest.fixture(autouse=True)
def database_schema(monkeypatch):
    name = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(BASE_URL, autocommit=True) as db:
        db.execute(f"CREATE SCHEMA {name}")
        db.execute(f"SET search_path={name}")
        db.execute(INIT_SQL.read_text(encoding="utf-8"))
    monkeypatch.setenv("CORPUS_DATABASE_URL", BASE_URL)
    monkeypatch.setenv("CORPUS_DB_SCHEMA", name)
    yield name
    with psycopg.connect(BASE_URL, autocommit=True) as db:
        db.execute(f"DROP SCHEMA {name} CASCADE")
