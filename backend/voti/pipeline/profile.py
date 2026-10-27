"""media.profile — Vox-Profile traits for a media's segments via the legacy-ml service.

Selects active segments with speaker_state in (single, unknown) whose *current* revision has no VoiceProfile for
the configured backbone (settings `pipeline.voxprofile_backbone`), sends them to legacy-ml in batches, writes one
VoiceProfile row per (segment, revision, backbone), refreshes Speaker.profile of the current cluster run, and
chains media.fingerprint.

Payload (all optional):
  chain: false        run as a single step (API after boundary edits) — no follow-up job
  only_missing: true  default behaviour anyway (kept for readability of API-enqueued jobs)
  force: true         recompute even when a profile for the current revision exists
  backbone: str       override the configured backbone
  reason: str         informational
"""
from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert

from ..db import session_scope
from ..models import Segment, VoiceProfile
from ..queue import JobContext, JobError, handler
from . import legacy_client
from .common import get_setting, normalized_key

log = logging.getLogger("voti.pipeline.profile")

BATCH_SPANS = 48          # spans per /voxprofile request (≈ 1–3 min of GPU work for whisper-large-v3)
MIN_SEC = 1.0             # the service returns null below this; don't ask
PROFILE_STATES = ("single", "unknown")
FIELDS = ("age", "age_group", "sex", "sex_prob", "voice_quality", "emotion", "emotion_dim", "speech_flow",
          "accent_broad", "accent_narrow")


def select_segments(media_id: uuid.UUID, backbone: str, force: bool = False) -> list[tuple[uuid.UUID, int, float, float]]:
    """(segment_id, revision, start, end) of segments needing a profile, in time order."""
    with session_scope() as s:
        q = select(Segment.id, Segment.revision, Segment.start_sec, Segment.end_sec).where(
            Segment.media_id == media_id, Segment.active.is_(True), Segment.speaker_state.in_(PROFILE_STATES),
            (Segment.end_sec - Segment.start_sec) >= MIN_SEC)
        if not force:
            q = q.outerjoin(VoiceProfile, and_(VoiceProfile.segment_id == Segment.id,
                                               VoiceProfile.revision == Segment.revision,
                                               VoiceProfile.backbone == backbone)
                            ).where(VoiceProfile.id.is_(None))
        return [tuple(r) for r in s.execute(q.order_by(Segment.start_sec)).all()]


def _row(seg_id: uuid.UUID, revision: int, backbone: str, res: dict, versions: dict) -> dict:
    row = {"segment_id": seg_id, "revision": revision, "backbone": backbone,
           "model_versions": {**versions, "n_chunks": res.get("n_chunks"), "low_confidence": res.get("low_confidence"),
                              "duration_sec": res.get("duration_sec"), "sex_probs": res.get("sex_probs")}}
    for f in FIELDS:
        v = res.get(f)
        row[f] = v if v is not None or f in ("age", "age_group", "sex", "sex_prob") else {}
    return row


def write_profiles(rows: list[dict]) -> None:
    if not rows:
        return
    with session_scope() as s:
        stmt = insert(VoiceProfile).values([{"id": uuid.uuid4(), **r} for r in rows])
        stmt = stmt.on_conflict_do_update(
            index_elements=["segment_id", "revision", "backbone"],
            set_={f: stmt.excluded[f] for f in (*FIELDS, "model_versions")})
        s.execute(stmt)


def profile_media(media_id: uuid.UUID, backbone: str | None = None, force: bool = False,
                  progress=lambda f, phase="", msg=None: None) -> dict:
    t0 = time.monotonic()
    if backbone is None:
        with session_scope() as s:
            backbone = get_setting(s, "pipeline").get("voxprofile_backbone") or "whisper-large-v3"
    if backbone not in legacy_client.BACKBONES:
        raise JobError("bad_setting", f"未知的 Vox-Profile backbone：{backbone}", retryable=False)
    todo = select_segments(media_id, backbone, force)
    key = normalized_key(media_id)
    done, low_conf, versions = 0, 0, {}
    progress(0.0, "voxprofile", f"{len(todo)} 段待分析（{backbone}）")
    for i in range(0, len(todo), BATCH_SPANS):
        batch = todo[i:i + BATCH_SPANS]
        out = legacy_client.voxprofile(key, [(a, b) for _, _, a, b in batch], backbone)
        versions = out.get("model_versions", {})
        rows = []
        for (seg_id, rev, _, _), res in zip(batch, out["results"]):
            if res is None:
                continue
            low_conf += bool(res.get("low_confidence"))
            rows.append(_row(seg_id, rev, backbone, res, versions))
        write_profiles(rows)
        done += len(rows)
        progress((i + len(batch)) / max(1, len(todo)), "voxprofile", f"{i + len(batch)}/{len(todo)}")
    return {"backbone": backbone, "candidates": len(todo), "profiled": done, "low_confidence": low_conf,
            "model_versions": versions, "elapsed_sec": round(time.monotonic() - t0, 2)}


def _refresh_speakers(media_id: uuid.UUID) -> int | None:
    """Re-aggregate Speaker.profile in the current cluster run (cluster module is optional)."""
    try:
        from . import cluster
    except ImportError:
        return None
    try:
        if hasattr(cluster, "refresh_profiles_for_media"):
            return cluster.refresh_profiles_for_media(media_id)
        if hasattr(cluster, "refresh_speaker_profiles"):
            from ..models import ClusterRun
            with session_scope() as s:
                run_id = s.scalar(select(ClusterRun.id).where(ClusterRun.is_current.is_(True))
                                  .order_by(ClusterRun.created_at.desc()))
            return cluster.refresh_speaker_profiles(run_id) if run_id else 0
    except Exception:  # noqa: BLE001 - profiles are written; speaker aggregation is best effort
        log.exception("refresh speaker profiles failed for media %s", media_id)
    return None


@handler("media.profile")
def run(ctx: JobContext) -> dict:
    from . import handlers as h  # shared media status / chaining helpers (lazy: avoids import cycles)

    h._begin(ctx)
    p = ctx.payload or {}
    result = profile_media(ctx.media_id, backbone=p.get("backbone"), force=bool(p.get("force")),
                           progress=lambda f, phase="", msg=None: ctx.progress(f, phase or None, msg))
    if result["profiled"]:
        result["speakers_refreshed"] = _refresh_speakers(ctx.media_id)
    if p.get("chain") is False:
        h._finish_single_step(ctx)
    else:
        h._chain(ctx)  # → media.fingerprint
    return result
