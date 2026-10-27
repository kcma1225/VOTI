"""Job handlers for the audio and ml queues (pipeline after media.download).

Every media step: Media.status="processing", Media.stage=<kind>; on success it chains the next kind in
voti.queue.PIPELINE via ctx.then (media.segment → media.align → media.embed → media.profile, implemented in pipeline/profile.py,
which chains media.fingerprint). The last step (media.fingerprint) sets Media.status="ready" and enqueues channel.dedup
(debounced). payload {"chain": false} runs a single step without chaining (used by the API for lazy
re-embedding after boundary edits); payload {"force": true} on media.embed recomputes all embeddings.
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import select

from .. import storage
from ..db import session_scope
from ..models import Asset, Job, Media, MediaQuality
from ..queue import PIPELINE, JobContext, JobError, handler
from . import audio as audio_mod
from .common import normalized_key, work_dir

log = logging.getLogger("voti.pipeline.handlers")


def _progress(ctx: JobContext):
    def cb(fraction: float, phase: str = "") -> None:
        ctx.progress(fraction, phase or None)
    return cb


def _begin(ctx: JobContext) -> None:
    if ctx.media_id is None:
        raise JobError("bad_job", "缺少 media_id", retryable=False)
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        if m is None:
            raise JobError("not_found", "找不到媒體", retryable=False)
        m.status, m.stage = "processing", ctx.kind
        m.failure_code = m.failure_message = None


def _chain(ctx: JobContext) -> None:
    if ctx.payload.get("chain") is False:
        return
    i = PIPELINE.index(ctx.kind)
    if i + 1 < len(PIPELINE):
        payload = {"reprocess": True} if ctx.payload.get("reprocess") else {}
        with session_scope() as s:
            m = s.get(Media, ctx.media_id)
            channel_id = m.channel_id if m else None
        ctx.then(PIPELINE[i + 1], payload, media_id=ctx.media_id, channel_id=channel_id)


def _finish_single_step(ctx: JobContext) -> None:
    """A non-chained single step leaves the media as it was before (ready stays ready)."""
    if ctx.payload.get("chain") is False:
        with session_scope() as s:
            m = s.get(Media, ctx.media_id)
            if m is not None and m.stage == ctx.kind:
                m.stage = None
                if _has_fingerprint(s, m.id):
                    m.status = "ready"


def _has_fingerprint(s, media_id: uuid.UUID) -> bool:
    return s.scalar(select(Asset.id).where(Asset.media_id == media_id, Asset.kind == "fingerprint")) is not None


# ───────────────────────────── media.normalize ─────────────────────────────

def normalize_media(media_id: uuid.UUID, progress=lambda f, p="": None) -> dict:
    t0 = time.monotonic()
    with session_scope() as s:
        asset = s.scalar(select(Asset).where(Asset.media_id == media_id, Asset.kind == "original_audio")
                         .order_by(Asset.created_at.desc()))
        if asset is None:
            raise JobError("original_missing", "找不到原始音訊（請先下載）", retryable=False)
        key = asset.key
    with work_dir("normalize") as wd:
        src = storage.get_file(key, wd / ("original" + (("." + key.rsplit(".", 1)[-1]) if "." in key else "")))
        try:
            info = audio_mod.probe(src)
            progress(0.05, "normalize")
            norm = audio_mod.normalize(src, wd / "normalized.wav", info["duration_sec"],
                                       lambda f: progress(0.05 + 0.6 * f, "normalize"))
            progress(0.7, "spectrum")
            spec = audio_mod.analyze_original(src, info["sample_rate"])
        except audio_mod.AudioError as e:
            raise JobError("audio_invalid", str(e), retryable=False) from e
        with session_scope() as s:
            out = storage.store_asset(s, wd / "normalized.wav", normalized_key(media_id), "normalized_audio",
                                      media_id=media_id, content_type="audio/wav",
                                      params={"source_asset_id": str(asset.id), "ffmpeg": audio_mod.ffmpeg_version(),
                                              "args": norm["ffmpeg_args"], "format": "wav/pcm_s16le/16000/mono"})
            mq = s.get(MediaQuality, media_id)
            if mq is None:
                mq = MediaQuality(media_id=media_id)
                s.add(mq)
            mq.sample_rate, mq.channels, mq.codec = info["sample_rate"], info["channels"], info["codec"]
            mq.bitrate, mq.container = info["bitrate"], info["container"]
            mq.real_bandwidth_hz = spec.get("bandwidth_hz")
            mq.upsampled_suspect = spec.get("upsampled_suspect")
            mq.lufs_integrated, mq.true_peak_db = norm["lufs_integrated"], norm["true_peak_db"]
            mq.clipping_ratio = spec.get("clipping_ratio")
            mq.details = {**(mq.details or {}),
                          # long-term average spectrum of the ORIGINAL audio: [{"hz", "db"}], log-spaced to Nyquist
                          "spectrum": spec.get("spectrum") or [],
                          "bandwidth": {k: spec.get(k) for k in ("bandwidth_hz", "nyquist_hz", "floor_db", "ref_db",
                                                                 "edge_drop_db", "sharp_edge", "frames_used")},
                          "probe": {"format_name": info["format_name"], "duration_sec": info["duration_sec"]}}
            m = s.get(Media, media_id)
            if m is not None and not m.duration_sec:
                m.duration_sec = info["duration_sec"]
            normalized_bytes = out.bytes
    return {"duration_sec": round(norm["normalized_duration_sec"], 3), "sample_rate": info["sample_rate"],
            "codec": info["codec"], "bandwidth_hz": spec.get("bandwidth_hz"),
            "upsampled_suspect": spec.get("upsampled_suspect"), "lufs": norm["lufs_integrated"],
            "true_peak_db": norm["true_peak_db"], "clipping_ratio": spec.get("clipping_ratio"),
            "normalized_bytes": normalized_bytes, "elapsed_sec": round(time.monotonic() - t0, 2)}


@handler("media.normalize")
def h_normalize(ctx: JobContext) -> dict:
    _begin(ctx)
    res = normalize_media(ctx.media_id, _progress(ctx))
    _chain(ctx)
    _finish_single_step(ctx)
    return res


@handler("media.quality")
def h_quality(ctx: JobContext) -> dict:
    from . import quality
    _begin(ctx)
    res = quality.run(ctx.media_id, _progress(ctx))
    _chain(ctx)
    _finish_single_step(ctx)
    return res


@handler("media.diarize")
def h_diarize(ctx: JobContext) -> dict:
    from . import diarize
    _begin(ctx)
    res = diarize.run(ctx.media_id, _progress(ctx))
    _chain(ctx)
    _finish_single_step(ctx)
    return res


@handler("media.segment")
def h_segment(ctx: JobContext) -> dict:
    from . import segment
    _begin(ctx)
    res = segment.run(ctx.media_id, _progress(ctx))
    _chain(ctx)
    _finish_single_step(ctx)
    return res


@handler("media.align")
def h_align(ctx: JobContext) -> dict:
    """Per-character timing (voti.pipeline.align). In the pipeline chain a missing aligner model never stops the
    media: the step records why it skipped and the chain continues (re-run later with POST /media/{id}/align).
    With payload chain=false (edit re-alignment, backfill) Media.status / stage are not touched."""
    from . import align
    chained = ctx.payload.get("chain") is not False
    if ctx.media_id is None:
        raise JobError("bad_job", "缺少 media_id", retryable=False)
    if chained:
        _begin(ctx)
    try:
        res = align.run(ctx.media_id, _progress(ctx), segment_ids=ctx.payload.get("segment_ids"),
                        force=bool(ctx.payload.get("force")), pending_only=bool(ctx.payload.get("pending_only")))
    except JobError as e:
        if not chained or e.code not in ("align_unavailable", "align_model_unavailable", "out_of_memory") \
                or ctx.will_retry(e):
            raise
        log.warning("media.align skipped for %s: %s %s", ctx.media_id, e.code, e.message[:200])
        res = {"skipped": True, "code": e.code, "message": e.message[:500]}
    _chain(ctx)
    return res


@handler("media.embed")
def h_embed(ctx: JobContext) -> dict:
    from . import embed
    _begin(ctx)
    # only_missing is the default behaviour: segments that already have an embedding for their revision are skipped
    res = embed.run(ctx.media_id, _progress(ctx), force=bool(ctx.payload.get("force")))
    _chain(ctx)
    _finish_single_step(ctx)
    return res


@handler("media.fingerprint")
def h_fingerprint(ctx: JobContext) -> dict:
    from . import fingerprint
    _begin(ctx)
    res = fingerprint.run_fingerprint(ctx.media_id, _progress(ctx))
    with session_scope() as s:
        m = s.get(Media, ctx.media_id)
        m.status, m.stage = "ready", None
        channel_id = m.channel_id
        if channel_id is not None:
            queued = s.scalar(select(Job.id).where(Job.kind == "channel.dedup", Job.channel_id == channel_id,
                                                   Job.status == "queued"))
            if queued is None:
                ctx.then("channel.dedup", {"trigger_media_id": str(ctx.media_id)}, channel_id=channel_id)
            res["dedup"] = "already_queued" if queued else "queued"
    return res


@handler("channel.dedup")
def h_dedup(ctx: JobContext) -> dict:
    from . import fingerprint
    if ctx.channel_id is None:
        raise JobError("bad_job", "缺少 channel_id", retryable=False)
    with session_scope() as s:  # debounce: a newer queued run will see everything this one would
        newer = s.scalar(select(Job.id).where(Job.kind == "channel.dedup", Job.channel_id == ctx.channel_id,
                                              Job.status == "queued", Job.id != ctx.job_id))
    if newer is not None:
        return {"skipped": True, "reason": "newer_dedup_queued", "job_id": str(newer)}
    return fingerprint.run_dedup(ctx.channel_id, _progress(ctx))


@handler("speakers.cluster")
def h_cluster(ctx: JobContext) -> dict:
    from . import cluster
    return cluster.run(ctx.payload or {}, ctx.created_by, _progress(ctx))
