"""Dataset filters → SQL (shared by preview and export) and the `dataset.export` job handler.

Filter schema: docs/ARCHITECTURE.md#dataset-filters. `DatasetFilters` fills defaults, so a saved dataset with
`{}` means "the recommended defaults".

Export layout under exports/<export_id>/:
  manifest.jsonl, manifest.csv, README.md         always
  dataset.tar                                      when include_audio: audio/<segment_id>_r<revision>.wav
                                                   (16 kHz mono PCM16 cut from media/<id>/normalized.wav)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import tarfile
import tempfile
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, cast, exists, func, null, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from . import storage
from .config import get_config
from .db import session_scope
from .models import (Channel, ClusterRun, Dataset, DatasetExport, Media, MediaQuality, Segment, Speaker,
                     SpeakerAssignment, SpeakerOverride, VoiceProfile, utcnow)
from .queue import JobContext, JobError, handler

log = logging.getLogger("voti.datasets")

EXPORT_FILES = ("manifest.jsonl", "manifest.csv", "README.md", "dataset.tar")


class DatasetFilters(BaseModel):
    model_config = ConfigDict(extra="ignore")
    channel_ids: list[uuid.UUID] = []
    media_ids: list[uuid.UUID] = []
    speaker_ids: list[uuid.UUID] = []
    text_sources: list[str] = ["manual_caption"]
    review_status: list[str] = ["passed", "unchecked"]
    exclude_repeated: bool = True
    max_repeated_overlap: float | None = Field(default=0.2, ge=0, le=1)
    speaker_states: list[str] = ["single", "unknown"]
    min_duration: float | None = 1.0
    max_duration: float | None = 20.0
    min_snr: float | None = 10
    min_c50: float | None = None
    min_dnsmos_ovrl: float | None = 2.8
    min_dnsmos_p808: float | None = None
    min_speech_ratio: float | None = 0.6
    min_bandwidth_hz: float | None = 7000
    exclude_upsampled: bool = False
    exclude_flags: list[str] = ["clipped"]
    licenses: list[str] = []
    sex: list[str] = []
    age_groups: list[str] = []
    max_sec_per_speaker: float | None = Field(default=None, gt=0)


def parse_filters(raw: dict | None) -> DatasetFilters:
    return DatasetFilters.model_validate(raw or {})


def _has_any(col, values: list[str]):  # noqa: ANN001, ANN202
    return col.op("?|")(cast(values, ARRAY(Text)))


_duration = Segment.end_sec - Segment.start_sec


def current_run_id(s: Session) -> uuid.UUID | None:
    return s.scalar(select(ClusterRun.id).where(ClusterRun.is_current.is_(True))
                    .order_by(ClusterRun.created_at.desc()).limit(1))


def conditions(f: DatasetFilters) -> dict[str, list]:
    """Named groups of WHERE clauses over Segment ⋈ Media ⟕ MediaQuality. Groups let preview report what
    each criterion excluded."""
    g: dict[str, list] = defaultdict(list)
    g["scope"].append(Segment.active.is_(True))
    if f.channel_ids:
        g["scope"].append(Media.channel_id.in_(f.channel_ids))
    if f.media_ids:
        g["scope"].append(Segment.media_id.in_(f.media_ids))
    if f.speaker_ids:
        g["scope"].append(exists().where(SpeakerAssignment.segment_id == Segment.id,
                                         SpeakerAssignment.speaker_id.in_(f.speaker_ids)))
    if f.licenses:
        g["scope"].append(Media.license.in_(f.licenses))
    if f.text_sources:
        g["text_source"].append(Segment.text_source.in_(f.text_sources))
    if f.review_status:
        g["review"].append(Segment.review_status.in_(f.review_status))
    if f.exclude_repeated:
        g["repeated"].append(Segment.repeated_overlap <= (f.max_repeated_overlap or 0.0))
    if f.speaker_states:
        g["speaker_state"].append(Segment.speaker_state.in_(f.speaker_states))
    if f.min_duration is not None:
        g["duration"].append(_duration >= f.min_duration)
    if f.max_duration is not None:
        g["duration"].append(_duration <= f.max_duration)
    q = g["quality"]
    for col, v in ((Segment.snr, f.min_snr), (Segment.c50, f.min_c50), (Segment.dnsmos_ovrl, f.min_dnsmos_ovrl),
                   (Segment.dnsmos_p808, f.min_dnsmos_p808), (Segment.speech_ratio, f.min_speech_ratio),
                   (MediaQuality.real_bandwidth_hz, f.min_bandwidth_hz)):
        if v is not None:
            q.append(col >= v)
    if f.exclude_upsampled:
        q.append(func.coalesce(MediaQuality.upsampled_suspect, False).is_(False))
    if f.exclude_flags:
        q.append(~_has_any(Segment.flags, f.exclude_flags))
    if f.sex or f.age_groups:
        pc = [VoiceProfile.segment_id == Segment.id, VoiceProfile.revision == Segment.revision]
        if f.sex:
            pc.append(VoiceProfile.sex.in_(f.sex))
        if f.age_groups:
            pc.append(VoiceProfile.age_group.in_(f.age_groups))
        g["profile"].append(exists().where(*pc))
    return g


def where(groups: dict[str, list], skip: set[str] = frozenset()):  # noqa: ANN201
    return and_(true(), *[c for name, cs in groups.items() if name not in skip for c in cs])


def _base_from(q):  # noqa: ANN001, ANN202
    return q.select_from(Segment).join(Media, Media.id == Segment.media_id) \
        .outerjoin(MediaQuality, MediaQuality.media_id == Segment.media_id)


@dataclass
class Row:
    segment_id: uuid.UUID
    media_id: uuid.UUID
    channel_id: uuid.UUID | None
    duration: float
    speaker_id: uuid.UUID | None
    dnsmos: float | None
    snr: float | None
    start_sec: float


def select_rows(s: Session, f: DatasetFilters) -> list[Row]:
    """All segments matching the filters, after the per-speaker cap. Deterministic order."""
    run_id = current_run_id(s)
    sp = select(SpeakerAssignment.speaker_id).where(SpeakerAssignment.segment_id == Segment.id,
                                                    SpeakerAssignment.run_id == run_id) \
        .limit(1).correlate(Segment).scalar_subquery() if run_id else null()
    cols = [Segment.id, Segment.media_id, Media.channel_id, _duration, sp, Segment.dnsmos_ovrl, Segment.snr,
            Segment.start_sec]
    q = _base_from(select(*cols)).where(where(conditions(f))) \
        .order_by(Media.channel_id, Segment.media_id, Segment.start_sec, Segment.id)
    rows = [Row(*r) for r in s.execute(q).all()]
    if f.max_sec_per_speaker:
        rows = cap_per_speaker(rows, f.max_sec_per_speaker)
    return rows


def cap_per_speaker(rows: list[Row], cap: float) -> list[Row]:
    """Keep the best (DNSMOS, SNR) segments of each candidate speaker up to `cap` seconds.
    Segments without a speaker are not capped."""
    def key(r: Row):  # noqa: ANN202
        return (-(r.dnsmos if r.dnsmos is not None else -1e9), -(r.snr if r.snr is not None else -1e9),
                str(r.segment_id))
    used: dict = defaultdict(float)
    keep: set = set()
    for r in sorted(rows, key=key):
        if r.speaker_id is None:
            keep.add(r.segment_id)
            continue
        if used[r.speaker_id] + r.duration <= cap + 1e-9:
            used[r.speaker_id] += r.duration
            keep.add(r.segment_id)
    return [r for r in rows if r.segment_id in keep]


def _count(s: Session, groups: dict[str, list], skip: set[str] = frozenset(), extra=None) -> int:  # noqa: ANN001
    q = _base_from(select(func.count())).where(where(groups, skip))
    if extra is not None:
        q = q.where(extra)
    return s.scalar(q) or 0


def preview(s: Session, raw_filters: dict | None) -> dict:
    f = parse_filters(raw_filters)
    rows = select_rows(s, f)
    groups = conditions(f)
    n_all = _count(s, groups)
    by_ch: dict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        by_ch[r.channel_id][0] += 1
        by_ch[r.channel_id][1] += r.duration
    titles = dict(s.execute(select(Channel.id, Channel.title).where(Channel.id.in_([c for c in by_ch if c])))
                  .all()) if by_ch else {}
    sexes = segment_sexes(s, [r.segment_id for r in rows])
    by_sex = {"male": 0.0, "female": 0.0, "unknown": 0.0}
    for r in rows:
        sx = sexes.get(r.segment_id)
        by_sex[sx if sx in ("male", "female") else "unknown"] += r.duration
    excluded = {
        "repeated": (_count(s, groups, {"repeated"}) - n_all) if f.exclude_repeated else 0,
        "auto_caption": _count(s, groups, {"text_source"}, Segment.text_source == "auto_caption")
        if "auto_caption" not in f.text_sources else 0,
        "low_quality": _count(s, groups, {"quality"}) - n_all,
        "speaker_cap": n_all - len(rows),
    }
    return {
        "segment_count": len(rows), "total_sec": round(sum(r.duration for r in rows), 3),
        "speakers": len({r.speaker_id for r in rows if r.speaker_id}),
        "channels": len({r.channel_id for r in rows if r.channel_id}),
        "by_channel": sorted([{"channel_id": c, "title": titles.get(c, "") if c else "（無頻道）", "segment_count": v[0],
                               "total_sec": round(v[1], 3)} for c, v in by_ch.items()],
                             key=lambda x: -x["total_sec"]),
        "by_sex": {k: round(v, 3) for k, v in by_sex.items()},
        "excluded": excluded,
        "filters": json.loads(f.model_dump_json()),
    }


def segment_sexes(s: Session, ids: list[uuid.UUID]) -> dict:
    out: dict = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        for seg_id, sex in s.execute(select(VoiceProfile.segment_id, VoiceProfile.sex)
                                     .join(Segment, and_(Segment.id == VoiceProfile.segment_id,
                                                         Segment.revision == VoiceProfile.revision))
                                     .where(VoiceProfile.segment_id.in_(chunk))
                                     .order_by(VoiceProfile.created_at)).all():
            out[seg_id] = sex
    return out


# ───────────────────────────── export ─────────────────────────────

CSV_COLUMNS = ["segment_id", "revision", "audio_path", "start_sec", "end_sec", "duration", "text", "text_source",
               "text_corrected", "language", "media_platform", "media_id", "media_url", "media_title",
               "media_published_at", "media_license", "channel_id", "channel_title", "speaker_id", "speaker_label",
               "speaker_display_name", "snr", "c50", "dnsmos_sig", "dnsmos_bak", "dnsmos_ovrl", "dnsmos_p808",
               "speech_ratio", "bandwidth_hz", "sample_rate", "age", "age_group", "sex", "emotion", "sha256"]


def _iso(dt) -> str | None:  # noqa: ANN001
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def manifest_rows(s: Session, rows: list[Row]) -> list[dict]:
    run_id = current_run_id(s)
    out: list[dict] = []
    for i in range(0, len(rows), 2000):
        ids = [r.segment_id for r in rows[i:i + 2000]]
        segs = {g.id: g for g in s.scalars(select(Segment).where(Segment.id.in_(ids)))}
        media_ids = {g.media_id for g in segs.values()}
        media = {m.id: m for m in s.scalars(select(Media).where(Media.id.in_(media_ids)))}
        quality = {q.media_id: q for q in s.scalars(select(MediaQuality).where(MediaQuality.media_id.in_(media_ids)))}
        ch_ids = {m.channel_id for m in media.values() if m.channel_id}
        channels = dict(s.execute(select(Channel.id, Channel.title).where(Channel.id.in_(ch_ids))).all()) \
            if ch_ids else {}
        speakers: dict = {}
        if run_id:
            for seg_id, sp in s.execute(select(SpeakerAssignment.segment_id, Speaker)
                                        .join(Speaker, Speaker.id == SpeakerAssignment.speaker_id)
                                        .where(SpeakerAssignment.run_id == run_id,
                                               SpeakerAssignment.segment_id.in_(ids))).all():
                speakers[seg_id] = sp
        overrides = dict(s.execute(select(SpeakerOverride.segment_id, SpeakerOverride.speaker_name)
                                   .where(SpeakerOverride.segment_id.in_(ids))).all())
        profiles: dict = {}
        for vp in s.scalars(select(VoiceProfile).join(Segment, and_(Segment.id == VoiceProfile.segment_id,
                                                                   Segment.revision == VoiceProfile.revision))
                            .where(VoiceProfile.segment_id.in_(ids)).order_by(VoiceProfile.created_at)):
            profiles[vp.segment_id] = vp
        for sid in ids:
            g = segs[sid]
            m = media[g.media_id]
            q = quality.get(m.id)
            sp = speakers.get(sid)
            vp = profiles.get(sid)
            speaker = None
            if sp is not None or sid in overrides:
                speaker = {"id": str(sp.id) if sp else None, "label": sp.label if sp else None,
                           "display_name": overrides.get(sid) or (sp.display_name if sp else None),
                           "override": sid in overrides}
            out.append({
                "segment_id": str(g.id), "revision": g.revision, "audio_path": None,
                "start_sec": round(g.start_sec, 3), "end_sec": round(g.end_sec, 3),
                "duration": round(g.end_sec - g.start_sec, 3),
                "text": g.corrected_text if g.corrected_text is not None else g.original_text,
                "text_source": g.text_source, "text_corrected": g.corrected_text is not None,
                "language": g.language,
                "media": {"platform": m.platform, "id": m.platform_media_id, "voti_id": str(m.id), "url": m.url,
                          "title": m.title, "published_at": _iso(m.published_at), "license": m.license},
                "channel": {"id": str(m.channel_id) if m.channel_id else None,
                            "title": channels.get(m.channel_id) if m.channel_id else None},
                "speaker": speaker,
                "quality": {"snr": g.snr, "c50": g.c50, "dnsmos_sig": g.dnsmos_sig, "dnsmos_bak": g.dnsmos_bak,
                            "dnsmos_ovrl": g.dnsmos_ovrl, "dnsmos_p808": g.dnsmos_p808,
                            "speech_ratio": g.speech_ratio,
                            "bandwidth_hz": q.real_bandwidth_hz if q else None,
                            "sample_rate": q.sample_rate if q else None},
                "profile": {"age": vp.age, "age_group": vp.age_group, "sex": vp.sex, "sex_prob": vp.sex_prob,
                            "emotion": (vp.emotion or {}).get("label"), "backbone": vp.backbone} if vp else None,
                "sha256": None,
            })
    return out


def csv_row(r: dict) -> dict:
    sp, q, p = r["speaker"] or {}, r["quality"], r["profile"] or {}
    return {"segment_id": r["segment_id"], "revision": r["revision"], "audio_path": r["audio_path"] or "",
            "start_sec": r["start_sec"], "end_sec": r["end_sec"], "duration": r["duration"], "text": r["text"],
            "text_source": r["text_source"], "text_corrected": r["text_corrected"], "language": r["language"] or "",
            "media_platform": r["media"]["platform"], "media_id": r["media"]["id"], "media_url": r["media"]["url"],
            "media_title": r["media"]["title"], "media_published_at": r["media"]["published_at"] or "",
            "media_license": r["media"]["license"] or "", "channel_id": r["channel"]["id"] or "",
            "channel_title": r["channel"]["title"] or "", "speaker_id": sp.get("id") or "",
            "speaker_label": sp.get("label") or "", "speaker_display_name": sp.get("display_name") or "",
            **{k: ("" if q[k] is None else q[k]) for k in ("snr", "c50", "dnsmos_sig", "dnsmos_bak", "dnsmos_ovrl",
                                                          "dnsmos_p808", "speech_ratio", "bandwidth_hz",
                                                          "sample_rate")},
            "age": "" if p.get("age") is None else p["age"], "age_group": p.get("age_group") or "",
            "sex": p.get("sex") or "", "emotion": p.get("emotion") or "", "sha256": r["sha256"] or ""}


def audio_path(r: dict) -> str:
    return f"audio/{r['segment_id']}_r{r['revision']}.wav"


def _scratch_dir() -> str | None:
    wd = get_config().work_dir
    try:
        os.makedirs(wd, exist_ok=True)
        return wd if os.access(wd, os.W_OK) else None
    except OSError:
        return None


def write_audio_tar(rows: list[dict], tar_path: Path, tmp: Path, progress=None) -> None:  # noqa: ANN001
    """Cut each row's clip from its media's normalized.wav into an uncompressed tar; fills audio_path/sha256."""
    by_media: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_media[r["media"]["voti_id"]].append(r)
    done = 0
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tar:
        for media_id, items in by_media.items():
            key = storage.media_key(media_id, "normalized.wav")
            local = tmp / f"{media_id}.wav"
            try:
                storage.get_file(key, local)
            except Exception as e:  # noqa: BLE001
                raise JobError("audio_missing", f"找不到 {key}：{e}", retryable=True) from e
            with sf.SoundFile(str(local)) as snd:
                sr, n = snd.samplerate, snd.frames
                for r in sorted(items, key=lambda x: x["start_sec"]):
                    f0 = max(0, min(n, int(round(r["start_sec"] * sr))))
                    f1 = max(f0, min(n, int(round(r["end_sec"] * sr))))
                    snd.seek(f0)
                    pcm = snd.read(f1 - f0, dtype="int16", always_2d=False)
                    buf = io.BytesIO()
                    sf.write(buf, pcm, sr, format="WAV", subtype="PCM_16")
                    data = buf.getvalue()
                    r["audio_path"] = audio_path(r)
                    r["sha256"] = hashlib.sha256(data).hexdigest()
                    if r["quality"]["sample_rate"] is None:
                        r["quality"]["sample_rate"] = sr
                    ti = tarfile.TarInfo(r["audio_path"])
                    ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
                    tar.addfile(ti, io.BytesIO(data))
                    done += 1
                    if progress:
                        progress(done)
            local.unlink(missing_ok=True)


def readme(name: str, description: str, export_id: uuid.UUID, filters: dict, stats: dict, include_audio: bool) -> str:
    lines = [f"# {name}", "", description or "", "",
             f"- Export ID: `{export_id}`", f"- Created: {_iso(utcnow())}",
             f"- Segments: {stats['segment_count']}", f"- Total duration: {stats['total_sec'] / 3600:.3f} h "
             f"({stats['total_sec']:.1f} s)", f"- Candidate speakers: {stats['speakers']}",
             f"- Channels: {stats['channels']}", "",
             "## Files", "",
             "- `manifest.jsonl` — one JSON object per segment (full metadata)",
             "- `manifest.csv` — the same rows flattened"]
    if include_audio:
        lines.append("- `dataset.tar` — `audio/<segment_id>_r<revision>.wav`, 16 kHz mono PCM16 clips; "
                     "`sha256` in the manifest is the hash of each wav file")
    lines += ["", "## Notes", "",
              "- `text` is the human-corrected text when `text_corrected` is true, else the caption text.",
              "- `speaker` is a *candidate* cluster from automatic speaker clustering, not a confirmed identity "
              "unless `override` is true (named by a reviewer).",
              "- `profile` (age/sex/emotion) are model predictions (Vox-Profile) and may be wrong.",
              "- Check each source's `media.license` and platform terms before redistributing audio.", "",
              "## By channel", "", "| channel | segments | hours |", "|---|---:|---:|"]
    for c in stats.get("by_channel", []):
        lines.append(f"| {c['title'] or c['channel_id']} | {c['segment_count']} | {c['total_sec'] / 3600:.3f} |")
    lines += ["", "## Filters", "", "```json", json.dumps(filters, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


@handler("dataset.export")
def export_handler(ctx: JobContext) -> dict:
    export_id = uuid.UUID(str(ctx.payload["export_id"]))
    with session_scope() as s:
        exp = s.get(DatasetExport, export_id)
        if exp is None:
            raise JobError("not_found", "找不到匯出紀錄", retryable=False)
        ds = s.get(Dataset, exp.dataset_id)
        exp.status = "running"
        filters = exp.filters or {}
        options = dict((exp.stats or {}).get("options") or {})
        name, description = (ds.name, ds.description) if ds else ("dataset", "")
    include_audio = bool(ctx.payload.get("include_audio", options.get("include_audio", True)))
    fmt = ctx.payload.get("format", options.get("format", "jsonl"))
    try:
        result = _run_export(ctx, export_id, filters, name, description, include_audio, fmt)
    except BaseException as e:
        with session_scope() as s:
            exp = s.get(DatasetExport, export_id)
            if exp is not None:
                exp.status = "failed"
                exp.stats = {**(exp.stats or {}), "error": f"{type(e).__name__}: {e}"[:2000]}
        raise
    return result


def _run_export(ctx: JobContext, export_id: uuid.UUID, filters: dict, name: str, description: str,
                include_audio: bool, fmt: str) -> dict:
    ctx.progress(0.0, "select", "篩選音段", force=True)
    with session_scope() as s:
        stats = preview(s, filters)
        rows = manifest_rows(s, select_rows(s, parse_filters(filters)))
    n = len(rows)
    prefix = f"exports/{export_id}"
    with tempfile.TemporaryDirectory(prefix="voti-export-", dir=_scratch_dir()) as td:
        tmp = Path(td)
        archive_key = None
        tar_path = tmp / "dataset.tar"
        if include_audio and rows:
            ctx.progress(0.05, "audio", f"切割 {n} 段音訊", force=True)
            write_audio_tar(rows, tar_path, tmp,
                            progress=lambda i: ctx.progress(0.05 + 0.85 * i / max(1, n), "audio", f"{i}/{n}"))
        ctx.progress(0.92, "manifest", "寫入清單", force=True)
        jsonl = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows).encode()
        sio = io.StringIO()
        w = csv.DictWriter(sio, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(csv_row(r))
        csv_bytes = ("﻿" + sio.getvalue()).encode()  # BOM so spreadsheet apps read UTF-8
        stats_out = json.loads(json.dumps({k: v for k, v in stats.items() if k != "filters"}, default=str))
        stats_out["options"] = {"include_audio": include_audio, "format": fmt}
        md = readme(name, description, export_id, stats["filters"], stats_out, include_audio and bool(rows)).encode()
        with session_scope() as s:
            a_jsonl = storage.store_bytes_asset(s, jsonl, f"{prefix}/manifest.jsonl", "export_manifest",
                                                content_type="application/x-ndjson; charset=utf-8",
                                                params={"export_id": str(export_id)})
            a_csv = storage.store_bytes_asset(s, csv_bytes, f"{prefix}/manifest.csv", "export_manifest",
                                              content_type="text/csv; charset=utf-8",
                                              params={"export_id": str(export_id)})
            storage.store_bytes_asset(s, md, f"{prefix}/README.md", "export_manifest",
                                      content_type="text/markdown; charset=utf-8", params={"export_id": str(export_id)})
            a_tar = None
            if include_audio and rows:
                a_tar = storage.store_asset(s, tar_path, f"{prefix}/dataset.tar", "export_archive",
                                            content_type="application/x-tar", params={"export_id": str(export_id)})
                archive_key = a_tar.key
            exp = s.get(DatasetExport, export_id)
            exp.status = "completed"
            exp.segment_count, exp.total_sec = stats["segment_count"], stats["total_sec"]
            exp.stats = stats_out
            exp.manifest_asset_id = (a_csv if fmt == "csv" else a_jsonl).id
            exp.archive_asset_id = a_tar.id if a_tar else None
    ctx.progress(1.0, "done", "完成", force=True)
    return {"export_id": str(export_id), "segment_count": n, "archive": archive_key}
