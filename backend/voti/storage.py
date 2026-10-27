"""Object storage (RustFS via the S3 API). Swap to MinIO/AWS by changing S3_* settings only.

Key layout (all under one bucket):
  media/<media_id>/info.json
  media/<media_id>/original.<ext>
  media/<media_id>/normalized.wav            16 kHz mono PCM16
  media/<media_id>/subtitles/<lang>.<kind>.<fmt>
  media/<media_id>/frames_quality.npz        per-frame arrays, see voti.pipeline.quality
  media/<media_id>/fingerprint.npz
  media/<media_id>/ocr/video.<ext>           temporary video for hard-subtitle OCR (deleted by media.ocr)
  media/<media_id>/ocr/events.json           OCR events (voti.pipeline.ocr), asset kind ocr_events
  media/<media_id>/ocr/crops/<idx>.webp      one small crop per OCR event
  exports/<export_id>/manifest.jsonl | manifest.csv | README.md | dataset.tar
  system/cookies.txt                          admin-uploaded yt-dlp cookies
"""
from __future__ import annotations

import hashlib
import uuid
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from .config import get_config
from .models import Asset


@lru_cache
def client():
    c = get_config()
    return boto3.client("s3", endpoint_url=c.s3_endpoint, aws_access_key_id=c.s3_access_key,
                        aws_secret_access_key=c.s3_secret_key, region_name=c.s3_region,
                        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"},
                                          retries={"max_attempts": 5, "mode": "standard"}))


def bucket() -> str:
    return get_config().s3_bucket


def ensure_bucket() -> None:
    try:
        client().head_bucket(Bucket=bucket())
    except ClientError:
        client().create_bucket(Bucket=bucket())


def media_key(media_id: uuid.UUID | str, name: str) -> str:
    return f"media/{media_id}/{name}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put_file(path: Path, key: str, content_type: str = "application/octet-stream") -> None:
    client().upload_file(str(path), bucket(), key, ExtraArgs={"ContentType": content_type})


def put_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> None:
    client().put_object(Bucket=bucket(), Key=key, Body=data, ContentType=content_type)


def get_file(key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    client().download_file(bucket(), key, str(dest))
    return dest


def get_bytes(key: str) -> bytes:
    return client().get_object(Bucket=bucket(), Key=key)["Body"].read()


def open_range(key: str, byte_range: str | None = None) -> dict:
    """Raw get_object response (Body, ContentLength, ContentRange, ContentType) for streaming to clients."""
    kwargs = {"Bucket": bucket(), "Key": key}
    if byte_range:
        kwargs["Range"] = byte_range
    return client().get_object(**kwargs)


def head(key: str) -> dict | None:
    try:
        return client().head_object(Bucket=bucket(), Key=key)
    except ClientError:
        return None


def delete(key: str) -> None:
    client().delete_object(Bucket=bucket(), Key=key)


def store_asset(s: Session, path: Path, key: str, kind: str, media_id: uuid.UUID | None = None,
                content_type: str = "application/octet-stream", params: dict | None = None) -> Asset:
    """Upload a local file and record (or replace) its Asset row. Same key → row updated in place."""
    digest = sha256_file(path)
    put_file(path, key, content_type)
    asset = s.query(Asset).filter_by(key=key).one_or_none()
    if asset is None:
        asset = Asset(key=key, kind=kind, media_id=media_id)
        s.add(asset)
    asset.sha256, asset.bytes, asset.content_type = digest, path.stat().st_size, content_type
    asset.params = params or {}
    s.flush()
    return asset


def store_bytes_asset(s: Session, data: bytes, key: str, kind: str, media_id: uuid.UUID | None = None,
                      content_type: str = "application/octet-stream", params: dict | None = None) -> Asset:
    put_bytes(data, key, content_type)
    asset = s.query(Asset).filter_by(key=key).one_or_none()
    if asset is None:
        asset = Asset(key=key, kind=kind, media_id=media_id)
        s.add(asset)
    asset.sha256, asset.bytes, asset.content_type = hashlib.sha256(data).hexdigest(), len(data), content_type
    asset.params = params or {}
    s.flush()
    return asset


def delete_prefix(prefix: str) -> int:
    """Delete every object under `prefix` (e.g. "media/<id>/"). Returns the number of objects deleted."""
    if not prefix or not prefix.endswith("/"):
        raise ValueError("prefix must be a non-empty directory-like prefix ending with '/'")
    deleted = 0
    for page in client().get_paginator("list_objects_v2").paginate(Bucket=bucket(), Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            client().delete_objects(Bucket=bucket(), Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
    return deleted


def upload_stream(fileobj: BinaryIO, key: str, content_type: str) -> None:
    client().upload_fileobj(fileobj, bucket(), key, ExtraArgs={"ContentType": content_type})
