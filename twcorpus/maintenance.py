"""Backup and integrity checks. Backups never include .env or model caches."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .core import asset_path
from .db import connect, rows, transaction
from .media import sha256


# Parents before children, so a restore can insert in this order.
TABLES = ("sources", "assets", "tracks", "cues", "segments", "segment_versions", "embeddings", "cluster_runs",
          "cluster_assignments", "cluster_overrides", "jobs", "review_history", "word_alignments", "exports")


def restore(settings: Settings, backup_dir: Path) -> dict:
    """Load a backup's table dumps into an empty database (schema from db/init.sql)."""
    counts = {}
    with connect(settings.database) as db, transaction(db):
        if db.execute("SELECT 1 FROM sources LIMIT 1").fetchone():
            raise ValueError("資料庫不是空的；請先重建資料庫再還原")
        for table in TABLES:
            kinds = {r["column_name"]: r["data_type"] for r in rows(
                db, "SELECT column_name,data_type FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name=?", (table,))}
            counts[table] = 0
            for line in (Path(backup_dir) / "database" / f"{table}.jsonl").read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                for column, value in record.items():
                    if kinds[column] == "bytea" and value is not None:
                        record[column] = bytes.fromhex(value)
                db.execute(f"INSERT INTO {table}({','.join(record)}) VALUES({','.join('?' for _ in record)})",
                           tuple(record.values()))
                counts[table] += 1
    return counts


def verify_assets(settings: Settings) -> dict:
    """Recompute SHA-256 for every registered asset; report missing or changed files."""
    with connect(settings.database) as db:
        records = rows(db, "SELECT id,path,sha256 FROM assets")
    missing, changed = [], []
    for record in records:
        try:
            path = asset_path(settings, record["path"])
        except ValueError:
            changed.append(record["path"])
            continue
        if not path.is_file():
            missing.append(record["path"])
        elif sha256(path) != record["sha256"]:
            changed.append(record["path"])
    return {"assets": len(records), "missing": missing, "changed": changed}


def backup(settings: Settings, target: Path) -> Path:
    """Consistent snapshot of every table (one repeatable-read transaction, JSON lines) plus the assets."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = target / f"corpus-backup-{stamp}"
    destination.mkdir(parents=True, exist_ok=False)
    dump = destination / "database"
    dump.mkdir()
    with connect(settings.database) as db:
        db.raw.autocommit = False
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table in TABLES:
            with (dump / f"{table}.jsonl").open("w", encoding="utf-8") as f:
                for record in rows(db, f"SELECT * FROM {table}"):
                    f.write(json.dumps({k: v.hex() if isinstance(v, (bytes, memoryview)) else v
                                        for k, v in record.items()}, ensure_ascii=False) + "\n")
        db.raw.rollback()
    if (settings.data_dir / "assets").is_dir():
        shutil.copytree(settings.data_dir / "assets", destination / "assets")
    (destination / "RESTORE.txt").write_text(
        "還原：把 assets/ 複製到新的 CORPUS_DATA_DIR，在空的資料庫執行 python -m twcorpus.cli restore <本目錄>，再執行 verify。\n"
        "未包含 .env、API key、模型快取與 exports；模型會在首次 embedding 時重新下載。\n", encoding="utf-8")
    return destination
