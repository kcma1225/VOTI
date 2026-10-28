"""/api/settings (admin only). Runtime settings live in the `settings` table; cookies.txt in S3.

YouTube Data API keys (`/settings/youtube-api/*`, row `youtube_api`, voti.sources.ytapi) are secrets: no response
ever contains a full key (only `ytapi.mask`), audit rows store masked keys, nothing logs them."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, File, Response, UploadFile
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import storage
from ..auth import require_admin
from ..db import get_session
from ..models import Job, Media, Setting, User, utcnow
from ..queue import enqueue
from ..sources import ytapi
from . import schemas as S
from .errors import ApiError
from .views import audit

router = APIRouter(prefix="/settings", tags=["settings"])

COOKIES_KEY = "system/cookies.txt"
MAX_COOKIES_BYTES = 1 << 20

SETTING_MODELS: dict[str, type[BaseModel]] = {
    "youtube": S.YoutubeSettings,
    "pipeline": S.PipelineSettings,
    "quality_thresholds": S.QualityThresholds,
    "text": S.TextSettings,
    "validation": S.ValidationSettings,
    "ocr": S.OcrSettings,
}


def get_setting(s: Session, key: str) -> BaseModel:
    """Stored value merged over defaults (unknown/invalid stored fields are dropped)."""
    model = SETTING_MODELS[key]
    row = s.get(Setting, key)
    stored = dict(row.value or {}) if row else {}
    stored = {k: v for k, v in stored.items() if k in model.model_fields}
    try:
        return model(**stored)
    except ValidationError:
        return model()


def cookies_status() -> S.CookiesStatus:
    h = storage.head(COOKIES_KEY)
    if not h:
        return S.CookiesStatus(present=False)
    lm = h.get("LastModified")
    return S.CookiesStatus(present=True, updated_at=lm.astimezone(timezone.utc) if lm else None)


@router.get("", response_model=S.SettingsOut)
def read_settings(_: User = Depends(require_admin), s: Session = Depends(get_session)):
    return S.SettingsOut(youtube=get_setting(s, "youtube"), pipeline=get_setting(s, "pipeline"),
                         quality_thresholds=get_setting(s, "quality_thresholds"), text=get_setting(s, "text"),
                         validation=get_setting(s, "validation"), ocr=get_setting(s, "ocr"),
                         cookies=cookies_status(), youtube_api=youtube_api_out(s))


# ───────────────────────────── YouTube Data API keys ─────────────────────────────

def _key_out(k: ytapi.ApiKey) -> S.YoutubeApiKeyOut:
    st = ytapi.key_status(k.id)
    added = None
    if k.added_at:
        try:
            added = datetime.fromisoformat(k.added_at)
        except ValueError:
            added = None
    return S.YoutubeApiKeyOut(id=k.id, masked=ytapi.mask(k.key), label=k.label, source=k.source,
                              added_at=added, **st)


def youtube_api_out(s: Session, backfill_queued: bool = False) -> S.YoutubeApiOut:
    from ..sources.handlers import active_api_job, api_pending
    keys = [_key_out(k) for k in ytapi.all_keys(s)]
    job = active_api_job(s, None)
    pending = s.scalar(select(func.count()).select_from(Media).where(*api_pending())) or 0
    langs_pending = s.scalar(select(func.count()).select_from(Media).where(
        Media.detailed_at.is_(None), Media.api_checked_at.isnot(None), Media.has_manual_captions.is_(True))) or 0
    return S.YoutubeApiOut(keys=keys, usable=sum(k.status in ("ok", "untested") for k in keys),
                           daily_quota=ytapi.DAILY_QUOTA, backfill_queued=backfill_queued,
                           backfill=S.YoutubeApiBackfill(pending=pending, active=job is not None,
                                                         job_id=job.id if job else None,
                                                         langs_pending=langs_pending))


def _masked(keys: list[dict]) -> list[dict]:
    return [{"id": k.get("id"), "masked": ytapi.mask(k.get("key") or ""), "label": k.get("label") or ""}
            for k in keys]


def queue_api_backfill(s: Session, user: User | None) -> tuple[int, Job | None, bool]:
    """The global media.api_detail pass over every media the API has not seen. → (pending, job, already active)"""
    from ..sources.handlers import active_api_job, api_pending
    pending = s.scalar(select(func.count()).select_from(Media).where(*api_pending())) or 0
    job = active_api_job(s, None)
    if job is not None:
        return pending, job, True
    if pending:
        job = enqueue(s, "media.api_detail", payload={"all": True, "backfill": True},
                      created_by=user.id if user else None)
    return pending, job, False


@router.get("/youtube-api", response_model=S.YoutubeApiOut)
def read_youtube_api(_: User = Depends(require_admin), s: Session = Depends(get_session)):
    return youtube_api_out(s)


@router.put("/youtube-api/keys", response_model=S.YoutubeApiOut)
def put_youtube_api_keys(body: S.YoutubeApiKeysIn, admin: User = Depends(require_admin),
                         s: Session = Depends(get_session)):
    """Replace the stored key list: `{id}` keeps a stored key (label may change), `{key}` adds one; stored keys
    not listed are removed. The env key (YOUTUBE_API_KEY) is not stored here and ignored. When the first usable
    key(s) arrive, the API backfill over every not-yet-detailed media starts."""
    row = s.get(Setting, ytapi.SETTING_KEY)
    current = ytapi.stored_keys(s)
    by_id = {k.get("id") or ytapi.key_id(k["key"]): k for k in current}
    had_usable = ytapi.has_usable_key(s)
    out: list[dict] = []
    seen: set[str] = set()
    for item in body.keys:
        if item.key:
            key = ytapi.normalize_key(item.key)
            if key is None:
                raise ApiError(422, "invalid_api_key", "API 金鑰格式不正確（應為 Google Cloud 的 API 金鑰，例如 AIza 開頭）")
            kid = ytapi.key_id(key)
            if kid in seen:
                continue
            prev = by_id.get(kid)
            entry = dict(prev) if prev else {"id": kid, "key": key, "added_at": utcnow().isoformat(),
                                             "added_by": str(admin.id)}
        elif item.id:
            if item.id in seen:
                continue
            if item.id not in by_id:
                if any(k.id == item.id and k.source == "env" for k in ytapi.all_keys(s)):
                    continue  # the env key is shown in the list but not stored
                raise ApiError(422, "unknown_api_key", "找不到這個 API 金鑰（可能已被移除），請重新整理頁面")
            entry = dict(by_id[item.id])
            kid = item.id
        else:
            raise ApiError(422, "invalid_api_key", "每一項需要 id 或 key")
        if item.label is not None:
            entry["label"] = item.label.strip()
        entry["id"] = kid
        seen.add(kid)
        out.append(entry)
    for k in current:
        kid = k.get("id") or ytapi.key_id(k["key"])
        if kid not in seen:
            ytapi.forget(kid)
    if row is None:
        row = Setting(key=ytapi.SETTING_KEY, value={})
        s.add(row)
    row.value, row.updated_by = {"keys": out}, admin.id
    audit(s, admin, "settings.youtube_api_keys", "setting", ytapi.SETTING_KEY, before={"keys": _masked(current)},
          after={"keys": _masked(out)})
    s.flush()
    started = False
    if not had_usable and ytapi.has_usable_key(s):
        _, job, active = queue_api_backfill(s, admin)
        started = job is not None and not active
    s.commit()
    return youtube_api_out(s, backfill_queued=started)


@router.post("/youtube-api/test", response_model=S.YoutubeApiTestOut)
def test_youtube_api_key(body: S.YoutubeApiTestIn, _: User = Depends(require_admin),
                         s: Session = Depends(get_session)):
    """One cheap videos.list call (1 quota unit) with a stored key ({id}) or a key not saved yet ({key})."""
    if body.id:
        k = next((k for k in ytapi.all_keys(s) if k.id == body.id), None)
        if k is None:
            raise ApiError(404, "unknown_api_key", "找不到這個 API 金鑰")
        res = ytapi.test_key(k.key, k.id)
        return S.YoutubeApiTestOut(**res, key=_key_out(k))
    key = ytapi.normalize_key(body.key or "")
    if key is None:
        raise ApiError(422, "invalid_api_key", "API 金鑰格式不正確（應為 Google Cloud 的 API 金鑰，例如 AIza 開頭）")
    return S.YoutubeApiTestOut(**ytapi.test_key(key))


@router.post("/youtube-api/backfill", response_model=S.YoutubeApiBackfillOut)
def youtube_api_backfill(admin: User = Depends(require_admin), s: Session = Depends(get_session)):
    """Run the API pass for every media whose caption info is not known yet (one chained global job)."""
    if not ytapi.has_usable_key(s):
        raise ApiError(409, "no_api_key", "沒有可用的 YouTube API 金鑰（未設定、配額用完或無效）")
    pending, job, active = queue_api_backfill(s, admin)
    if job is not None and not active:
        audit(s, admin, "settings.youtube_api_backfill", "setting", ytapi.SETTING_KEY, after={"pending": pending})
    s.commit()
    return S.YoutubeApiBackfillOut(queued=pending, already_active=active, job_id=job.id if job else None)


def _looks_like_cookies(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    if lines[0].startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File")):
        return True
    data_lines = [ln for ln in lines if not ln.startswith("#") or ln.startswith("#HttpOnly_")]
    return bool(data_lines) and all(len(ln.split("\t")) == 7 for ln in data_lines)


@router.put("/cookies", response_model=S.CookiesStatus)
async def upload_cookies(file: UploadFile = File(...), admin: User = Depends(require_admin),
                         s: Session = Depends(get_session)):
    data = await file.read(MAX_COOKIES_BYTES + 1)
    if len(data) > MAX_COOKIES_BYTES:
        raise ApiError(413, "file_too_large", "cookies.txt 超過 1 MB")
    if not _looks_like_cookies(data):
        raise ApiError(400, "invalid_cookies", "檔案不是 Netscape 格式的 cookies.txt")
    storage.put_bytes(data, COOKIES_KEY, "text/plain")
    audit(s, admin, "settings.cookies_upload", "setting", "cookies", after={"bytes": len(data)})
    s.commit()
    return cookies_status()


@router.delete("/cookies", status_code=204, response_class=Response)
def delete_cookies(admin: User = Depends(require_admin), s: Session = Depends(get_session)):
    storage.delete(COOKIES_KEY)
    audit(s, admin, "settings.cookies_delete", "setting", "cookies")
    s.commit()
    return Response(status_code=204)


@router.put("/{key}", response_model=dict)
def put_setting(key: str, value: dict = Body(...), admin: User = Depends(require_admin),
                s: Session = Depends(get_session)):
    model = SETTING_MODELS.get(key)
    if model is None:
        raise ApiError(404, "unknown_setting", "沒有這個設定項目")
    current = get_setting(s, key)
    try:
        new = model(**{**current.model_dump(), **value})
    except ValidationError as e:
        raise ApiError(422, "invalid_setting", f"設定值不正確：{e.errors()[0].get('msg', '')}") from None
    if isinstance(new, S.PipelineSettings) and new.segment_min_sec >= new.segment_max_sec:
        raise ApiError(422, "invalid_setting", "segment_min_sec 必須小於 segment_max_sec")
    row = s.get(Setting, key)
    if row is None:
        row = Setting(key=key, value={})
        s.add(row)
    row.value, row.updated_by = new.model_dump(), admin.id
    audit(s, admin, "settings.update", "setting", key, before=current.model_dump(), after=new.model_dump())
    s.commit()
    return new.model_dump()
