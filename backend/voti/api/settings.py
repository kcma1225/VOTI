"""/api/settings (admin only). Runtime settings live in the `settings` table; cookies.txt in S3."""
from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Body, Depends, File, Response, UploadFile
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from .. import storage
from ..auth import require_admin
from ..db import get_session
from ..models import Setting, User
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
                         quality_thresholds=get_setting(s, "quality_thresholds"), cookies=cookies_status())


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
