"""Single local configuration source. Paths never come from remote metadata."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DATABASE_URL = "postgresql://corpus:corpus@127.0.0.1:5432/corpus"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str = field(default_factory=lambda: os.environ.get("CORPUS_DATABASE_URL", DEFAULT_DATABASE_URL))
    db_schema: str = field(default_factory=lambda: os.environ.get("CORPUS_DB_SCHEMA", "public"))
    youtube_api_key: str = ""
    speaker_device: str = "cpu"
    min_segment_sec: float = 0.5
    max_segment_sec: float = 30.0
    edge_padding_sec: float = 0.12

    @property
    def database(self) -> str:
        """libpq connection string; the schema is a search_path so tests can use an isolated one."""
        from psycopg.conninfo import make_conninfo
        return make_conninfo(self.database_url, options=f"-c search_path={self.db_schema}")


def load_settings() -> Settings:
    # Keep .env optional; never print secret values in UI or logs.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    return Settings(
        data_dir=Path(os.environ.get("CORPUS_DATA_DIR", "./data")).expanduser().resolve(),
        database_url=os.environ.get("CORPUS_DATABASE_URL", DEFAULT_DATABASE_URL),
        youtube_api_key=os.environ.get("YOUTUBE_API_KEY", ""),
        speaker_device=os.environ.get("SPEAKER_DEVICE", "cpu"),
        min_segment_sec=float(os.environ.get("MIN_SEGMENT_SEC", "0.5")),
        max_segment_sec=float(os.environ.get("MAX_SEGMENT_SEC", "30")),
        edge_padding_sec=float(os.environ.get("EDGE_PADDING_SEC", "0.12")),
    )
