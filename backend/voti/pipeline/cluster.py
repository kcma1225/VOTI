"""`speakers.cluster`: agglomerative clustering (cosine, average linkage) of current-revision ECAPA embeddings.

payload {"threshold": t, "scope": {"channel_ids": [...]}}. `threshold` is a cosine *similarity* threshold
(0..1, larger = stricter; ~0.5-0.65 is typical for ECAPA), the same scale the UI slider shows; internally the
clustering works on cosine distance = 1 - similarity. Human overrides (SpeakerOverride) are respected:
segments sharing a speaker_name enter clustering as one unit (forced into the same cluster), and a cluster that
ends up with two different names is split between them (nearest named centroid). Large inputs (> DIRECT_MAX
units) are first compressed into k-means micro-clusters, which are then clustered agglomeratively.

Writes ClusterRun (is_current; previous runs set false), Speaker rows, SpeakerAssignment rows, and
Speaker.profile aggregated from VoiceProfile rows (`refresh_speaker_profiles`, callable later when profiles
arrive).
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable

import numpy as np
from sqlalchemy import select, update

from ..db import session_scope
from ..models import (ClusterRun, Embedding, Media, Segment, Speaker, SpeakerAssignment, SpeakerOverride,
                      VoiceProfile)
from .common import get_setting
from .embed import MODEL_ID

log = logging.getLogger("voti.pipeline.cluster")

DIRECT_MAX = 8000
MICRO_CLUSTERS = 4000
DEFAULT_THRESHOLD = 0.6  # cosine similarity


def _unit(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-9)


def _agglomerative(vectors: np.ndarray, threshold: float) -> np.ndarray:
    n = vectors.shape[0]
    if n == 0:
        return np.zeros(0, dtype=int)
    if n == 1:
        return np.zeros(1, dtype=int)
    if n <= DIRECT_MAX:
        from sklearn.cluster import AgglomerativeClustering
        return AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                       distance_threshold=threshold).fit_predict(vectors)
    from sklearn.cluster import MiniBatchKMeans
    km = MiniBatchKMeans(n_clusters=MICRO_CLUSTERS, random_state=0, batch_size=4096, n_init=3).fit(vectors)
    micro = km.labels_
    cents = _unit(np.stack([vectors[micro == k].mean(axis=0) if np.any(micro == k) else km.cluster_centers_[k]
                            for k in range(MICRO_CLUSTERS)]))
    from sklearn.cluster import AgglomerativeClustering
    top = AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average",
                                  distance_threshold=threshold).fit_predict(cents)
    return top[micro]


def cluster_vectors(vectors: np.ndarray, names: list[str | None], threshold: float) -> np.ndarray:
    """Cluster unit vectors; names[i] = override speaker_name or None. Returns a cluster label per row.
    Pure function (unit-tested)."""
    vectors = _unit(vectors)
    n = vectors.shape[0]
    if n == 0:
        return np.zeros(0, dtype=int)
    # units: one per distinct name, one per unnamed row
    unit_of = np.empty(n, dtype=int)
    unit_vecs, unit_names = [], []
    name_unit: dict[str, int] = {}
    for i, nm in enumerate(names):
        if nm:
            if nm not in name_unit:
                name_unit[nm] = len(unit_vecs)
                unit_vecs.append(None)
                unit_names.append(nm)
            unit_of[i] = name_unit[nm]
        else:
            unit_of[i] = len(unit_vecs)
            unit_vecs.append(vectors[i])
            unit_names.append(None)
    for nm, u in name_unit.items():
        unit_vecs[u] = _unit(vectors[[i for i in range(n) if names[i] == nm]].mean(axis=0))
    U = np.stack(unit_vecs)
    ulabels = _agglomerative(U, threshold)
    # cannot-link: a cluster containing several names is split between them
    next_label = int(ulabels.max()) + 1 if ulabels.size else 0
    for c in np.unique(ulabels):
        members = np.flatnonzero(ulabels == c)
        named = [u for u in members if unit_names[u]]
        if len(named) <= 1:
            continue
        named_vecs = U[named]
        new_labels = {named[0]: int(c)}
        for u in named[1:]:
            new_labels[u] = next_label
            next_label += 1
        for u in members:
            if unit_names[u]:
                ulabels[u] = new_labels[u]
            else:
                ulabels[u] = new_labels[named[int(np.argmax(named_vecs @ U[u]))]]
    return ulabels[unit_of]


# ───────────────────────────── profiles ─────────────────────────────

def _label_probs(d: dict | None) -> dict[str, float]:
    """{"label": x, "probs": {...}} | {"labels": [...], "probs": {...}} | {label: p} → {label: p}."""
    if not isinstance(d, dict) or not d:
        return {}
    if isinstance(d.get("probs"), dict):
        return {str(k): float(v) for k, v in d["probs"].items() if isinstance(v, (int, float))}
    if "label" in d and isinstance(d["label"], str):
        return {d["label"]: 1.0}
    return {str(k): float(v) for k, v in d.items() if isinstance(v, (int, float))}


def aggregate_profiles(items: list[tuple[float, VoiceProfile]]) -> dict:
    """Duration-weighted aggregate of VoiceProfile rows → Speaker.profile (flat {label: prob} dicts)."""
    if not items:
        return {}
    sex, age_group, emotion, vq = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    vq_w = defaultdict(float)
    age_sum = age_w = total = 0.0
    for w, p in items:
        w = max(w, 1e-3)
        total += w
        if p.sex:
            if p.sex_prob is not None and p.sex in ("male", "female"):
                sex[p.sex] += w * p.sex_prob
                sex["female" if p.sex == "male" else "male"] += w * (1 - p.sex_prob)
            else:
                sex[p.sex] += w
        if p.age is not None:
            age_sum += w * p.age
            age_w += w
        if p.age_group:
            age_group[p.age_group] += w
        for k, v in _label_probs(p.emotion).items():
            emotion[k] += w * v
        for k, v in _label_probs(p.voice_quality).items():
            vq[k] += w * v
            vq_w[k] += w

    def norm(d: dict) -> dict:
        s = sum(d.values())
        return {k: round(v / s, 4) for k, v in sorted(d.items(), key=lambda kv: -kv[1])} if s > 0 else {}
    return {"sex": norm(sex), "age_mean": round(age_sum / age_w, 2) if age_w else None,
            "age_group": norm(age_group), "emotion": norm(emotion),
            "voice_quality": {k: round(vq[k] / vq_w[k], 4) for k in sorted(vq)},
            "profiled_segments": len(items), "profiled_sec": round(total, 2)}


def refresh_speaker_profiles(run_id: uuid.UUID) -> int:
    """(Re)aggregate Speaker.profile for every speaker of a run from current-revision VoiceProfiles.
    Prefers the configured Vox-Profile backbone, falls back to any backbone. Returns speakers updated."""
    with session_scope() as s:
        backbone = get_setting(s, "pipeline").get("voxprofile_backbone", "whisper-large-v3")
        rows = s.execute(select(SpeakerAssignment.speaker_id, Segment.start_sec, Segment.end_sec, VoiceProfile)
                         .join(Segment, Segment.id == SpeakerAssignment.segment_id)
                         .join(VoiceProfile, (VoiceProfile.segment_id == Segment.id)
                               & (VoiceProfile.revision == Segment.revision))
                         .where(SpeakerAssignment.run_id == run_id)).all()
        per_seg: dict[tuple, VoiceProfile] = {}
        for spk, a, b, vp in rows:
            k = (spk, vp.segment_id)
            if k not in per_seg or vp.backbone == backbone:
                per_seg[k] = (b - a, vp)
        by_spk = defaultdict(list)
        for (spk, _), item in per_seg.items():
            by_spk[spk].append(item)
        n = 0
        for sp in s.scalars(select(Speaker).where(Speaker.run_id == run_id)):
            prof = aggregate_profiles(by_spk.get(sp.id, []))
            if prof != (sp.profile or {}):
                sp.profile = prof
                n += 1
    return n


def refresh_profiles_for_media(media_id: uuid.UUID) -> int:
    """Convenience for media.profile: refresh speakers of the current run that contain this media's segments."""
    with session_scope() as s:
        run = s.scalar(select(ClusterRun).where(ClusterRun.is_current.is_(True)).order_by(ClusterRun.created_at.desc()))
        if run is None:
            return 0
        run_id = run.id
    return refresh_speaker_profiles(run_id)


# ───────────────────────────── job ─────────────────────────────

def run(payload: dict, created_by: uuid.UUID | None = None,
        progress: Callable[[float, str], None] = lambda f, p: None) -> dict:
    t0 = time.monotonic()
    threshold = float(payload.get("threshold", DEFAULT_THRESHOLD) or DEFAULT_THRESHOLD)  # similarity
    distance = 1.0 - threshold
    scope = payload.get("scope") or {}
    channel_ids = [uuid.UUID(str(c)) for c in scope.get("channel_ids") or []]
    with session_scope() as s:
        q = (select(Segment.id, Segment.media_id, Segment.start_sec, Segment.end_sec, Media.channel_id,
                    Embedding.vector)
             .join(Embedding, (Embedding.segment_id == Segment.id) & (Embedding.revision == Segment.revision)
                   & (Embedding.model_id == MODEL_ID))
             .join(Media, Media.id == Segment.media_id)
             .where(Segment.active.is_(True)))
        if channel_ids:
            q = q.where(Media.channel_id.in_(channel_ids))
        rows = s.execute(q).all()
        overrides = {o.segment_id: o.speaker_name for o in s.scalars(select(SpeakerOverride))}
        # override segments without an embedding still get assigned to their named speaker
        oq = (select(Segment.id, Segment.media_id, Segment.start_sec, Segment.end_sec, Media.channel_id)
              .join(Media, Media.id == Segment.media_id)
              .where(Segment.id.in_(list(overrides)), Segment.active.is_(True)))
        if channel_ids:
            oq = oq.where(Media.channel_id.in_(channel_ids))
        override_rows = s.execute(oq).all() if overrides else []
    progress(0.2, "cluster")
    seg_ids = [r.id for r in rows]
    vecs = _unit(np.asarray([np.asarray(r.vector, dtype=np.float32) for r in rows], dtype=np.float32)
                 .reshape(-1, 192))
    names = [overrides.get(sid) for sid in seg_ids]
    labels = cluster_vectors(vecs, names, distance)

    info = {r.id: (r.media_id, r.end_sec - r.start_sec, r.channel_id) for r in rows}
    members: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        members[int(lab)].append(i)
    # named clusters for override segments lacking embeddings
    name_cluster = {}
    for lab, idx in members.items():
        for i in idx:
            if names[i]:
                name_cluster[names[i]] = lab
    extra: dict[int, list[uuid.UUID]] = defaultdict(list)
    next_lab = max(members) + 1 if members else 0
    for r in override_rows:
        if r.id in info:
            continue
        nm = overrides[r.id]
        if nm not in name_cluster:
            name_cluster[nm] = next_lab
            members[next_lab] = []
            next_lab += 1
        extra[name_cluster[nm]].append(r.id)
        info[r.id] = (r.media_id, r.end_sec - r.start_sec, r.channel_id)

    def total_sec(lab: int) -> float:
        return sum(info[seg_ids[i]][1] for i in members[lab]) + sum(info[x][1] for x in extra[lab])
    ordered = sorted(members, key=lambda lab: -total_sec(lab))
    progress(0.7, "save")
    with session_scope() as s:
        s.execute(update(ClusterRun).where(ClusterRun.is_current.is_(True)).values(is_current=False))
        run = ClusterRun(model_id=MODEL_ID, algorithm="agglomerative-average-cosine", threshold=threshold,
                         scope={"channel_ids": [str(c) for c in channel_ids]} if channel_ids else {},
                         params={"metric": "cosine", "linkage": "average", "threshold_kind": "similarity", "distance_threshold": distance,
                                 "direct_max": DIRECT_MAX, "micro_clusters": MICRO_CLUSTERS if len(rows) > DIRECT_MAX else None},
                         is_current=True, created_by=created_by)
        s.add(run)
        s.flush()
        n_single = 0
        for rank, lab in enumerate(ordered, start=1):
            idx = members[lab]
            seg_list = [seg_ids[i] for i in idx] + extra[lab]
            centroid = _unit(vecs[idx].mean(axis=0)) if idx else None
            name_counts = defaultdict(int)
            for sid in seg_list:
                if overrides.get(sid):
                    name_counts[overrides[sid]] += 1
            display = max(name_counts, key=name_counts.get) if name_counts else None
            chans = sorted({str(info[x][2]) for x in seg_list if info[x][2] is not None})
            sp = Speaker(run_id=run.id, label=f"cand_{rank:03d}", display_name=display, verified=False,
                         segment_count=len(seg_list), total_sec=round(total_sec(lab), 3),
                         media_count=len({info[x][0] for x in seg_list}), channel_ids=chans,
                         centroid=centroid.tolist() if centroid is not None else None)
            s.add(sp)
            s.flush()
            if len(seg_list) == 1:
                n_single += 1
            for i in idx:
                sim = float(vecs[i] @ centroid) if centroid is not None else 0.0
                s.add(SpeakerAssignment(run_id=run.id, segment_id=seg_ids[i], speaker_id=sp.id,
                                        similarity=round(sim, 4), manual=seg_ids[i] in overrides))
            for x in extra[lab]:
                s.add(SpeakerAssignment(run_id=run.id, segment_id=x, speaker_id=sp.id, similarity=0.0, manual=True))
        stats = {"segments": len(rows) + sum(len(v) for v in extra.values()), "speakers": len(ordered),
                 "singletons": n_single,
                 "overrides": sum(1 for x in seg_ids if x in overrides) + sum(len(v) for v in extra.values()), "elapsed_sec": round(time.monotonic() - t0, 2)}
        run.stats = stats
        run_id = run.id
    refresh_speaker_profiles(run_id)
    return {"run_id": str(run_id), **stats}
