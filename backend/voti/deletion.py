"""Channel deletion: impact preview and the delete itself (DB rows + objects in RustFS).

Deleting a channel removes its playlists, every media row listed under it and — through ON DELETE CASCADE —
their assets, tracks, cues, quality, VAD/diarization, segments, embeddings, voice profiles, speaker
assignments, repeated regions and jobs. Dataset definitions and finished exports are kept (exports are
self-contained snapshots). Candidate-speaker stats of every cluster run are recomputed afterwards.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import storage
from .models import (Asset, Channel, Dataset, Job, Media, Playlist, Segment, Speaker, SpeakerAssignment, utcnow)

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
