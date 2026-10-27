"""/api/sources/resolve: classify a pasted URL (no network), create placeholder rows, enqueue source.resolve."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..models import Channel, Media, Playlist, User
from ..queue import enqueue
from . import schemas as S
from .errors import ApiError
from .views import channel_summaries, job_out, media_summaries

router = APIRouter(prefix="/sources", tags=["sources"])


def _classify(url: str) -> dict:
    try:
        from ..sources.youtube import classify_url  # lazy: owned by the fetch module
    except ImportError:
        raise ApiError(503, "resolver_unavailable", "網址解析模組尚未安裝") from None
    try:
        return classify_url(url)
    except ValueError as e:
        raise ApiError(400, "unsupported_url", f"不支援的網址：{e}") from None


def _get_or_create(s: Session, model, unique: dict, defaults: dict):  # noqa: ANN001, ANN202
    obj = s.scalar(select(model).filter_by(**unique))
    if obj is None:
        obj = model(**unique, **defaults)
        try:
            with s.begin_nested():
                s.add(obj)
                s.flush()
        except IntegrityError:  # raced: someone inserted it first
            obj = s.scalar(select(model).filter_by(**unique))
            if obj is None:
                raise
    return obj


def _playlist_out(s: Session, p: Playlist) -> S.PlaylistOut:
    from .channels import playlist_out
    return playlist_out(s, p)


@router.post("/resolve", response_model=S.ResolveOut)
def resolve(body: S.ResolveIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    url = body.url.strip()
    info = _classify(url)
    kind = info.get("type")
    canonical = info.get("canonical_url") or url
    out: dict = {"type": kind}
    ids: dict = {}
    if kind == "video" and info.get("video_id"):
        m = _get_or_create(s, Media, {"platform": "youtube", "platform_media_id": info["video_id"]},
                           {"url": canonical, "title": "", "status": "listed",
                            "kind": info.get("kind_hint") if info.get("kind_hint") in ("short", "live") else "video"})
        ids["media_id"] = m.id
        out["media"] = m
    elif kind == "channel":
        if info.get("channel_id"):
            c = _get_or_create(s, Channel, {"platform": "youtube", "platform_channel_id": info["channel_id"]},
                               {"url": canonical, "handle": info.get("handle")})
            if info.get("handle") and not c.handle:
                c.handle = info["handle"]
            ids["channel_id"] = c.id
            out["channel"] = c
    elif kind == "playlist" and info.get("playlist_id"):
        p = _get_or_create(s, Playlist, {"platform": "youtube", "platform_playlist_id": info["playlist_id"]},
                           {"url": canonical})
        out["playlist"] = p
    elif kind not in ("video", "channel", "playlist"):
        raise ApiError(400, "unsupported_url", "不支援的網址類型")
    # for a bare @handle nothing is created; the job resolves it. Per-media jobs are deduplicated by enqueue.
    job = enqueue(s, "source.resolve", payload={"url": url}, created_by=user.id, **ids)
    s.commit()
    return S.ResolveOut(
        type=kind, job=job_out(s, job),
        media=media_summaries(s, [out["media"]])[0] if out.get("media") else None,
        channel=channel_summaries(s, [out["channel"]])[0] if out.get("channel") else None,
        playlist=_playlist_out(s, out["playlist"]) if out.get("playlist") else None)
