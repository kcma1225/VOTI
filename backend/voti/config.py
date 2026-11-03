"""Runtime configuration, read once from the environment (.env via compose).

Values that admins may change at runtime (rate limits, Vox-Profile backbone, cookies) live in the
`settings` table instead; see voti.models.Setting and docs/ARCHITECTURE.md.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Public site
    public_hosts: str = "voti.gugulu.tw"          # comma separated; localhost/127.0.0.1 always allowed
    trusted_proxies: str = "*"                    # forwarded-allow-ips for uvicorn
    session_days: int = 14

    # Initial admin (applied only when the users table is empty)
    admin_username: str = "admin"
    admin_password: str = ""

    # Database / queue
    database_url: str = "postgresql+psycopg://voti:voti@db:5432/voti"
    redis_url: str = "redis://redis:6379/0"

    # Object storage (RustFS, S3 compatible)
    s3_endpoint: str = "http://rustfs:9000"
    s3_access_key: str = "voti"
    s3_secret_key: str = "voti-secret"
    s3_bucket: str = "voti"
    s3_region: str = "us-east-1"

    # ML
    device: str = "auto"                          # auto | cpu | cuda
    hf_token: str = ""
    legacy_ml_url: str = "http://legacy-ml:8600"  # Brouhaha + Vox-Profile inference service
    model_cache: str = "/models"

    # YouTube
    youtube_api_key: str = ""

    # Local scratch for workers (ffmpeg etc.)
    work_dir: str = "/work"

    @property
    def allowed_hosts(self) -> list[str]:
        hosts = [h.strip() for h in self.public_hosts.split(",") if h.strip()]
        return hosts + ["localhost", "127.0.0.1"]


@lru_cache
def get_config() -> Config:
    return Config()
