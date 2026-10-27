"""/api/channels, /api/playlists, /api/channels/{id}/repeated|dedup, /api/repeated/{group_id}."""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import textconv
from ..auth import current_user, require_admin
from ..deletion import channel_impact, delete_channel
from ..db import get_session
from ..models import Channel, Job, Media, MediaQuality, Playlist, PlaylistItem, RepeatedRegion, Segment, Speaker, User
from ..queue import enqueue
from . import schemas as S
from .errors import ApiError
from .views import (Paging, audit, channel_summaries, current_run_id, get_or_404, job_out, paging)

router = APIRouter(tags=["channels"])


@router.get("/channels", response_model=S.Page[S.ChannelSummary])
def list_channels(q: str | None = None, page: Paging = Depends(paging), _: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    cond = []
    if q:
        pat = f"%{q.strip()}%"
        cond.append(or_(Channel.title.ilike(pat), Channel.handle.ilike(pat), Channel.platform_channel_id.ilike(pat)))
    total = s.scalar(select(func.count()).select_from(Channel).where(*cond))
    rows = s.scalars(select(Channel).where(*cond).order_by(Channel.created_at.desc(), Channel.id)
                     .limit(page.limit).offset(page.offset)).all()
    return S.Page[S.ChannelSummary](items=channel_summaries(s, rows), total=total or 0)


def channel_detail(s: Session, c: Channel) -> S.ChannelDetail:
    summary = channel_summaries(s, [c])[0]
    run_id = current_run_id(s)
    sp = S.ChannelSpeakers()
    if run_id:
        cnt, tot = s.execute(select(func.count(), func.coalesce(func.sum(Speaker.total_sec), 0.0))
                             .where(Speaker.run_id == run_id, Speaker.channel_ids.contains([str(c.id)]))).one()
        sp = S.ChannelSpeakers(candidates=cnt, total_sec=round(tot, 3))
    qrow = s.execute(select(func.avg(MediaQuality.snr_mean), func.avg(MediaQuality.dnsmos_ovrl),
                            func.avg(MediaQuality.c50_mean), func.avg(MediaQuality.speech_ratio),
                            func.avg(MediaQuality.real_bandwidth_hz), func.count())
                     .join(Media, Media.id == MediaQuality.media_id).where(Media.channel_id == c.id)).one()
    quality = S.ChannelQuality(snr_mean=qrow[0], dnsmos_ovrl=qrow[1], c50_mean=qrow[2], speech_ratio=qrow[3],
                               real_bandwidth_hz=qrow[4], media_with_quality=qrow[5])
    kind_counts = {"video": 0, "short": 0}
    kind_counts.update(dict(s.execute(select(Media.kind, func.count()).where(Media.channel_id == c.id)
                                      .group_by(Media.kind)).all()))
    playlist_count = s.scalar(select(func.count()).select_from(Playlist).where(Playlist.channel_id == c.id)) or 0
    return S.ChannelDetail(**summary.model_dump(), description=c.description, banner_url=c.banner_url,
                           country=c.country, tags=c.tags or [], notes=c.notes, metadata=c.metadata_ or {},
                           created_at=c.created_at, speakers=sp, quality=quality, kind_counts=kind_counts,
                           playlist_count=playlist_count)


@router.get("/channels/{channel_id}", response_model=S.ChannelDetail)
def get_channel(channel_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return channel_detail(s, get_or_404(s, Channel, channel_id, "頻道"))


def _active_job(s: Session, kind: str, channel_id: uuid.UUID) -> Job | None:
    return s.scalar(select(Job).where(Job.kind == kind, Job.channel_id == channel_id, Job.media_id.is_(None),
                                      Job.status.in_(("queued", "running"))).limit(1))


@router.post("/channels/{channel_id}/refresh", response_model=S.JobOut)
def refresh_channel(channel_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    from ..sources.handlers import queue_channel_listing
    c = get_or_404(s, Channel, channel_id, "頻道")
    job = queue_channel_listing(s, c, created_by=user.id)  # the active listing job when there is one
    s.commit()
    return job_out(s, job)


@router.post("/channels/{channel_id}/align", response_model=S.QueuedOut)
def align_channel(channel_id: uuid.UUID, body: S.AlignIn | None = None, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    """Queue media.align (outside the pipeline chain) for every media of the channel that has active segments
    with text and at least one segment without a valid alignment (all of them with force=true)."""
    from ..models import WordAlignment
    from ..pipeline.align import text_hash
    get_or_404(s, Channel, channel_id, "頻道")
    force = bool(body and body.force)
    todo: set = set()
    rows = s.execute(select(Segment.media_id, Segment.revision, Segment.original_text, Segment.corrected_text,
                            WordAlignment.revision, WordAlignment.text_hash, WordAlignment.status)
                     .outerjoin(WordAlignment, WordAlignment.segment_id == Segment.id)
                     .join(Media, Media.id == Segment.media_id)
                     .where(Media.channel_id == channel_id, Segment.active.is_(True))).all()
    for mid, rev, orig, corr, wrev, whash, wstatus in rows:
        if mid in todo:
            continue
        text = corr if corr is not None else (orig or "")
        if force or wstatus is None or wstatus == "pending" or wrev != rev or whash != text_hash(text):
            todo.add(mid)
    for mid in sorted(todo, key=str):
        enqueue(s, "media.align", media_id=mid, channel_id=channel_id, created_by=user.id,
                payload={"chain": False, "force": force, "reason": "backfill"})
    s.commit()
    return S.QueuedOut(queued=len(todo))


@router.post("/channels/{channel_id}/validate", response_model=S.QueuedOut)
def validate_channel(channel_id: uuid.UUID, body: S.ValidateIn | None = None, user: User = Depends(current_user),
                     s: Session = Depends(get_session)):
    """Queue media.validate (outside the pipeline chain) for every media of the channel with active segments."""
    from .jobs import queue_validation
    get_or_404(s, Channel, channel_id, "頻道")
    n = queue_validation(s, user, Media.channel_id == channel_id, force=bool(body and body.force))
    s.commit()
    return S.QueuedOut(queued=n)


@router.post("/channels/{channel_id}/detail", response_model=S.QueuedOut)
def detail_channel(channel_id: uuid.UUID, body: S.ChannelDetailIn | None = None, user: User = Depends(current_user),
                   s: Session = Depends(get_session)):
    c = get_or_404(s, Channel, channel_id, "頻道")
    q = select(Media).where(Media.channel_id == c.id)
    if body and body.media_ids is not None:
        q = q.where(Media.id.in_(body.media_ids))
    else:
        q = q.where(Media.detailed_at.is_(None))
    n = 0
    for m in s.scalars(q):
        enqueue(s, "media.detail", media_id=m.id, channel_id=c.id, created_by=user.id)
        n += 1
    s.commit()
    return S.QueuedOut(queued=n)


@router.post("/channels/{channel_id}/convert-text", response_model=S.QueuedOut)
def convert_channel_text(channel_id: uuid.UUID, user: User = Depends(current_user),
                         s: Session = Depends(get_session)):
    """Queue media.convert_text for every downloaded media of the channel with Simplified (or unexamined
    Chinese) caption tracks."""
    get_or_404(s, Channel, channel_id, "頻道")
    if not textconv.text_settings(s)["convert_simplified"]:
        raise ApiError(409, "text_conversion_disabled", "簡轉繁已在設定中停用")
    ids = textconv.convertible_media(s, Media.channel_id == channel_id)
    for mid in sorted(ids, key=str):
        enqueue(s, "media.convert_text", media_id=mid, channel_id=channel_id, created_by=user.id)
    s.commit()
    return S.QueuedOut(queued=len(ids))


@router.patch("/channels/{channel_id}", response_model=S.ChannelDetail)
def patch_channel(channel_id: uuid.UUID, body: S.ChannelPatchIn, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    c = get_or_404(s, Channel, channel_id, "頻道")
    audit(s, user, "channel.notes", "channel", c.id, before={"notes": c.notes}, after={"notes": body.notes})
    c.notes = body.notes
    s.commit()
    return channel_detail(s, c)


@router.get("/channels/{channel_id}/delete-impact", response_model=S.ChannelDeleteImpact)
def get_delete_impact(channel_id: uuid.UUID, _: User = Depends(require_admin), s: Session = Depends(get_session)):
    get_or_404(s, Channel, channel_id, "頻道")
    return channel_impact(s, channel_id)


@router.delete("/channels/{channel_id}", response_model=S.ChannelDeleteOut)
def remove_channel(channel_id: uuid.UUID, confirm: str | None = None, admin: User = Depends(require_admin),
                   s: Session = Depends(get_session)):
    """Admin only. When anything was downloaded or reviewed, `confirm` must equal the channel title."""
    c = get_or_404(s, Channel, channel_id, "頻道")
    impact = channel_impact(s, c.id)
    if impact["requires_confirmation"] and (confirm or "").strip() != (c.title or "").strip():
        raise ApiError(409, "confirmation_required",
                       f"此頻道已有 {impact['downloaded_media']} 部影片下載或處理過，刪除會一併永久刪除音檔與分析結果；"
                       "請輸入頻道名稱確認")
    title = c.title
    result = delete_channel(s, c)
    audit(s, admin, "channel.delete", "channel", channel_id, before={"title": title, **impact}, after=None)
    s.commit()
    return result


# ───────────────────────────── playlists ─────────────────────────────

def playlist_outs(s: Session, rows) -> list[S.PlaylistOut]:  # noqa: ANN001
    ids = [p.id for p in rows]
    counts = dict(s.execute(select(PlaylistItem.playlist_id, func.count()).where(PlaylistItem.playlist_id.in_(ids))
                            .group_by(PlaylistItem.playlist_id)).all()) if ids else {}
    out = []
    for p in rows:
        meta = p.metadata_ or {}
        thumb = meta.get("thumbnail_url") or meta.get("thumbnail")
        if not thumb and isinstance(meta.get("thumbnails"), list) and meta["thumbnails"]:
            last = meta["thumbnails"][-1]
            thumb = last.get("url") if isinstance(last, dict) else None
        out.append(S.PlaylistOut(id=p.id, platform=p.platform, platform_playlist_id=p.platform_playlist_id,
                                 channel_id=p.channel_id, title=p.title, description=p.description, url=p.url,
                                 list_status=p.list_status, last_listed_at=p.last_listed_at,
                                 created_at=p.created_at, media_count=counts.get(p.id, 0),
                                 thumbnail_url=thumb if isinstance(thumb, str) else None))
    return out


def playlist_out(s: Session, p: Playlist) -> S.PlaylistOut:
    return playlist_outs(s, [p])[0]


@router.get("/playlists", response_model=S.Page[S.PlaylistOut])
def list_playlists(channel_id: uuid.UUID | None = None, page: Paging = Depends(paging),
                   _: User = Depends(current_user), s: Session = Depends(get_session)):
    cond = [Playlist.channel_id == channel_id] if channel_id else []
    total = s.scalar(select(func.count()).select_from(Playlist).where(*cond)) or 0
    rows = s.scalars(select(Playlist).where(*cond).order_by(Playlist.created_at.desc(), Playlist.id)
                     .limit(page.limit).offset(page.offset)).all()
    return S.Page[S.PlaylistOut](items=playlist_outs(s, rows), total=total)


@router.get("/playlists/{playlist_id}", response_model=S.PlaylistOut)
def get_playlist(playlist_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    return playlist_out(s, get_or_404(s, Playlist, playlist_id, "播放清單"))


# ───────────────────────────── repeated regions ─────────────────────────────

@router.get("/channels/{channel_id}/repeated", response_model=list[S.RepeatedGroup])
def channel_repeated(channel_id: uuid.UUID, _: User = Depends(current_user), s: Session = Depends(get_session)):
    get_or_404(s, Channel, channel_id, "頻道")
    return repeated_groups(s, RepeatedRegion.channel_id == channel_id)


def repeated_groups(s: Session, cond) -> list[S.RepeatedGroup]:  # noqa: ANN001
    groups: dict = defaultdict(list)
    for r in s.scalars(select(RepeatedRegion).where(cond)
                       .order_by(RepeatedRegion.group_id, RepeatedRegion.start_sec)):
        groups[r.group_id].append(r)
    ex_media = {r.media_id for rows in groups.values() for r in rows[:5]}
    titles = dict(s.execute(select(Media.id, Media.title).where(Media.id.in_(ex_media))).all()) if ex_media else {}
    out = []
    for gid, rows in groups.items():
        positions = [r.position for r in rows]
        out.append(S.RepeatedGroup(
            group_id=gid, position=max(set(positions), key=positions.count),
            duration=round(sum(r.end_sec - r.start_sec for r in rows) / len(rows), 3),
            match_count=max(r.match_count for r in rows), region_count=len(rows),
            excluded=all(r.excluded for r in rows),
            examples=[S.RepeatedExample(media_id=r.media_id, media_title=titles.get(r.media_id, ""), start=r.start_sec,
                                        end=r.end_sec) for r in rows[:5]]))
    out.sort(key=lambda g: (-g.region_count, g.position))
    return out


def recompute_repeated_overlap(s: Session, media_ids: set[uuid.UUID]) -> int:
    """Segment.repeated_overlap = fraction covered by excluded repeated regions; keeps the `repeated` flag in sync."""
    if not media_ids:
        return 0
    regions: dict = defaultdict(list)
    for r in s.scalars(select(RepeatedRegion).where(RepeatedRegion.media_id.in_(media_ids),
                                                    RepeatedRegion.excluded.is_(True))):
        regions[r.media_id].append((r.start_sec, r.end_sec))
    n = 0
    for seg in s.scalars(select(Segment).where(Segment.media_id.in_(media_ids), Segment.active.is_(True))):
        dur = seg.end_sec - seg.start_sec
        covered = _covered(seg.start_sec, seg.end_sec, regions.get(seg.media_id, []))
        ov = round(min(1.0, covered / dur), 4) if dur > 0 else 0.0
        flags = [f for f in (seg.flags or []) if f != "repeated"]
        if ov > 0:
            flags.append("repeated")
        if ov != seg.repeated_overlap or flags != list(seg.flags or []):
            seg.repeated_overlap, seg.flags = ov, flags
            n += 1
    return n


def _covered(start: float, end: float, spans: list[tuple[float, float]]) -> float:
    parts = sorted((max(start, a), min(end, b)) for a, b in spans if b > start and a < end)
    total, cur_s, cur_e = 0.0, None, None
    for a, b in parts:
        if cur_e is None or a > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = a, b
        else:
            cur_e = max(cur_e, b)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


@router.patch("/repeated/{group_id}", response_model=S.RepeatedPatchOut, tags=["repeated"])
def patch_repeated(group_id: uuid.UUID, body: S.RepeatedPatchIn, user: User = Depends(current_user),
                   s: Session = Depends(get_session)):
    rows = s.scalars(select(RepeatedRegion).where(RepeatedRegion.group_id == group_id)).all()
    if not rows:
        raise ApiError(404, "not_found", "找不到重複片段群組")
    before = all(r.excluded for r in rows)
    for r in rows:
        r.excluded = body.excluded
    s.flush()
    n = recompute_repeated_overlap(s, {r.media_id for r in rows})
    audit(s, user, "repeated.exclude", "repeated_group", group_id, before={"excluded": before},
          after={"excluded": body.excluded, "regions": len(rows)})
    s.commit()
    group = repeated_groups(s, RepeatedRegion.group_id == group_id)[0]
    return S.RepeatedPatchOut(**group.model_dump(), regions=len(rows), segments_updated=n)


@router.post("/channels/{channel_id}/dedup", response_model=S.JobOut)
def dedup_channel(channel_id: uuid.UUID, user: User = Depends(current_user), s: Session = Depends(get_session)):
    c = get_or_404(s, Channel, channel_id, "頻道")
    job = _active_job(s, "channel.dedup", c.id) or enqueue(s, "channel.dedup", channel_id=c.id, created_by=user.id)
    s.commit()
    return job_out(s, job)
