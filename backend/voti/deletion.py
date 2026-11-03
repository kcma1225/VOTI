"""Channel deletion and clearing a video's processed data: impact preview and the delete itself (DB + RustFS).

Deleting a channel removes its playlists, every media row listed under it and — through ON DELETE CASCADE —
their assets, tracks, cues, quality, VAD/diarization, segments, embeddings, voice profiles, speaker
assignments, repeated regions and jobs. Dataset definitions and finished exports are kept (exports are
self-contained snapshots). Candidate-speaker stats of every cluster run are recomputed afterwards.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from . import storage
from .models import (Asset, Channel, Dataset, DiarizationTurn, Job, Media, MediaQuality, Playlist, RepeatedRegion,
                     Segment, Speaker, SpeakerAssignment, SpeechRegion, Track, utcnow)

log = logging.getLogger("voti.deletion")

# Media that has any of these objects stored counts as "downloaded" for the warning.
DOWNLOADED_KINDS = ("original_audio", "normalized_audio")
ACTIVE = ("queued", "running")


def channel_impact(s: Session, channel_id: uuid.UUID) -> dict:
    media_ids = select(Media.id).where(Media.channel_id == channel_id)
    media_total = s.scalar(select(func.count()).select_from(Media).where(Media.channel_id == channel_id)) or 0
    dl_ids = select(Asset.media_id).where(Asset.media_id.in_(media_ids), Asset.kind.in_(DOWNLOADED_KINDS)).distinct()
    downloaded, downloaded_sec = s.execute(
        select(func.count(), func.coalesce(func.sum(Media.duration_sec), 0.0)).where(Media.id.in_(dl_ids))).one()
    seg_total, seg_reviewed = s.execute(
        select(func.count(), func.count().filter(Segment.review_status != "unchecked"))
        .where(Segment.media_id.in_(media_ids), Segment.active.is_(True))).one()
    storage_bytes = s.scalar(select(func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.media_id.in_(media_ids)))
    active_jobs = s.scalar(select(func.count()).select_from(Job).where(
        Job.status.in_(ACTIVE), (Job.channel_id == channel_id) | Job.media_id.in_(media_ids))) or 0
    datasets = s.scalar(select(func.count()).select_from(Dataset)
                        .where(Dataset.filters["channel_ids"].contains([str(channel_id)]))) or 0
    playlists = s.scalar(select(func.count()).select_from(Playlist).where(Playlist.channel_id == channel_id)) or 0
    return {"media_total": media_total, "playlists": playlists, "downloaded_media": downloaded,
            "downloaded_sec": round(float(downloaded_sec or 0), 1), "segments": seg_total,
            "reviewed_segments": seg_reviewed, "storage_bytes": int(storage_bytes or 0),
            "active_jobs": active_jobs, "datasets_referencing": datasets,
            "requires_confirmation": downloaded > 0 or seg_reviewed > 0}


def delete_channel(s: Session, channel: Channel) -> dict:
    """Delete everything belonging to the channel. Caller commits."""
    cid = channel.id
    impact = channel_impact(s, cid)
    media_ids = list(s.scalars(select(Media.id).where(Media.channel_id == cid)))
    # Stop work first: running jobs see their row vanish (or cancel_requested) at the next progress() and stop.
    s.execute(update(Job).where(Job.status.in_(ACTIVE), (Job.channel_id == cid) | Job.media_id.in_(media_ids))
              .values(cancel_requested=True, status="cancelled", finished_at=utcnow(),
                      error_code="cancelled", error_message="頻道已刪除"))
    affected_runs = list(s.scalars(select(SpeakerAssignment.run_id).join(Segment, Segment.id == SpeakerAssignment.segment_id)
                                   .where(Segment.media_id.in_(media_ids)).distinct()))
    if media_ids:
        s.execute(delete(Media).where(Media.id.in_(media_ids)))      # cascades to everything per-media
    s.execute(delete(Playlist).where(Playlist.channel_id == cid))
    s.execute(delete(Job).where(Job.channel_id == cid))
    s.delete(channel)
    s.flush()
    for run_id in affected_runs:
        _refresh_run_speakers(s, run_id)
    s.flush()
    # Objects last: if the DB part fails nothing is lost. Objects of a media are all under media/<id>/.
    objects = 0
    for mid in media_ids:
        try:
            objects += storage.delete_prefix(f"media/{mid}/")
        except Exception:  # noqa: BLE001 — DB rows are gone; orphaned objects are harmless and logged
            log.exception("failed to delete objects of media %s", mid)
    impact["objects_deleted"] = objects
    return impact


def _refresh_run_speakers(s: Session, run_id: uuid.UUID) -> None:
    """Recompute candidate-speaker counts after segments disappeared; drop speakers left empty."""
    stats = {r.speaker_id: r for r in s.execute(
        select(SpeakerAssignment.speaker_id, func.count().label("n"),
               func.coalesce(func.sum(Segment.end_sec - Segment.start_sec), 0.0).label("sec"),
               func.count(func.distinct(Segment.media_id)).label("media"),
               func.array_agg(func.distinct(Media.channel_id)).label("channels"))
        .join(Segment, Segment.id == SpeakerAssignment.segment_id)
        .join(Media, Media.id == Segment.media_id)
        .where(SpeakerAssignment.run_id == run_id)
        .group_by(SpeakerAssignment.speaker_id))}
    for sp in s.scalars(select(Speaker).where(Speaker.run_id == run_id)):
        r = stats.get(sp.id)
        if r is None:
            s.delete(sp)
            continue
        sp.segment_count, sp.total_sec, sp.media_count = r.n, round(float(r.sec), 3), r.media
        sp.channel_ids = sorted(str(c) for c in r.channels if c is not None)


# ───────────────────────────── single videos: clear processed data ─────────────────────────────
# "Deleting a video" removes what downloading and processing produced; the video itself (metadata, channel list
# entry, playlist membership, info.json) stays and becomes not downloaded again (status detailed / listed).

KEEP_ASSET_KINDS = ("info_json",)                         # listing / detail metadata, not processing output
PROCESSED_STATUSES = ("downloading", "downloaded", "processing", "ready")
CLEARED_METADATA = ("download", "ocr", "text_conversion")


def processed_condition():  # noqa: ANN201
    """Media that has anything to clear: a processing status or any stored object besides info.json."""
    has_assets = select(Asset.id).where(Asset.media_id == Media.id, Asset.kind.notin_(KEEP_ASSET_KINDS)).exists()
    return or_(Media.status.in_(PROCESSED_STATUSES), has_assets)


def processed_ids(s: Session, media_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    return list(s.scalars(select(Media.id).where(Media.id.in_(list(media_ids)), processed_condition())))


def media_impact(s: Session, media_ids: list[uuid.UUID]) -> dict:
    """What clearing these media removes. media_total: media with processed data (others are left alone);
    unprocessed: selected media without anything to clear."""
    ids = processed_ids(s, media_ids)
    dl_ids = select(Asset.media_id).where(Asset.media_id.in_(ids), Asset.kind.in_(DOWNLOADED_KINDS)).distinct()
    downloaded, downloaded_sec = s.execute(
        select(func.count(), func.coalesce(func.sum(Media.duration_sec), 0.0)).where(Media.id.in_(dl_ids))).one()
    seg_total, seg_reviewed = s.execute(
        select(func.count(), func.count().filter(Segment.review_status != "unchecked"))
        .where(Segment.media_id.in_(ids), Segment.active.is_(True))).one()
    storage_bytes = s.scalar(select(func.coalesce(func.sum(Asset.bytes), 0))
                             .where(Asset.media_id.in_(ids), Asset.kind.notin_(KEEP_ASSET_KINDS)))
    active_jobs = s.scalar(select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE),
                                                                      Job.media_id.in_(ids))) or 0
    selected = s.scalar(select(func.count()).select_from(Media).where(Media.id.in_(list(media_ids)))) or 0
    return {"media_total": len(ids), "unprocessed": selected - len(ids), "downloaded_media": downloaded,
            "downloaded_sec": round(float(downloaded_sec or 0), 1), "segments": seg_total,
            "reviewed_segments": seg_reviewed, "storage_bytes": int(storage_bytes or 0),
            "active_jobs": active_jobs, "requires_confirmation": downloaded > 0 or seg_reviewed > 0}


def clear_media(s: Session, media_ids: list[uuid.UUID]) -> dict:
    """Remove the downloaded audio and every processing result of these media (assets except info.json, caption
    tracks/cues, quality, VAD, diarization, repeated regions, segments with their alignments, embeddings, voice
    profiles and speaker assignments, the media's jobs) and their objects; the media rows stay and go back to
    detailed (or listed). Media without processed data are left alone. Caller commits."""
    ids = processed_ids(s, media_ids)
    impact = media_impact(s, ids)
    if not ids:
        return {**impact, "objects_deleted": 0}
    s.execute(update(Job).where(Job.status.in_(ACTIVE), Job.media_id.in_(ids))
              .values(cancel_requested=True, status="cancelled", finished_at=utcnow(),
                      error_code="cancelled", error_message="影片處理資料已刪除"))
    affected_runs = list(s.scalars(select(SpeakerAssignment.run_id)
                                   .join(Segment, Segment.id == SpeakerAssignment.segment_id)
                                   .where(Segment.media_id.in_(ids)).distinct()))
    keep_keys = set(s.scalars(select(Asset.key).where(Asset.media_id.in_(ids), Asset.kind.in_(KEEP_ASSET_KINDS))))
    for model in (Segment, Track, MediaQuality, SpeechRegion, DiarizationTurn, RepeatedRegion, Job):
        s.execute(delete(model).where(model.media_id.in_(ids)))  # cascades: cues, alignments, embeddings, …
    s.execute(delete(Asset).where(Asset.media_id.in_(ids), Asset.kind.notin_(KEEP_ASSET_KINDS)))
    for m in s.scalars(select(Media).where(Media.id.in_(ids))):
        m.status, m.stage = ("detailed" if m.detailed_at else "listed"), None
        m.failure_code = m.failure_message = None
        m.metadata_ = {k: v for k, v in (m.metadata_ or {}).items() if k not in CLEARED_METADATA}
    s.flush()
    for run_id in affected_runs:
        _refresh_run_speakers(s, run_id)
    s.flush()
    objects = 0
    for mid in ids:
        try:
            objects += storage.delete_prefix(f"media/{mid}/", keep=keep_keys)
        except Exception:  # noqa: BLE001 — DB rows are gone; orphaned objects are harmless and logged
            log.exception("failed to delete objects of media %s", mid)
    impact["objects_deleted"] = objects
    return impact
