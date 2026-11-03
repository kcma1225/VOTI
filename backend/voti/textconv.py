"""Simplified → Traditional Chinese (Taiwan) conversion of caption text with OpenCC.

Settings key `text` (docs/ARCHITECTURE.md "Runtime settings"):
    {"convert_simplified": true, "opencc_config": "s2twp"}

Which tracks are converted (`track_script`):
  * zh-Hans, zh-Hans-*, zh-CN, zh-SG, zh-MY (and cmn-… equivalents)  → Simplified by language tag: converted
  * zh-TW, zh-Hant(-*), zh-HK, zh-MO                                  → Traditional: never touched
  * zh, cmn, zh-<other>                                               → decided by content (`detect_script`)
  * anything else (nan-TW Taiwanese Hokkien in Han characters, hak, yue, en …) → never touched

Ingestion (sources.handlers._store_download → `convert_parsed_cues`): Cue.raw_text keeps the original, Cue.text
gets the converted cleaned text and the flag "s2t_converted" (only on cues whose text changed). Segments built
from such cues get the same flag (pipeline.segment). The decision is recorded in the subtitle asset's
params["s2t"] and summarised in Media.metadata["text_conversion"].

Retroactive (`media.convert_text` job, queue audio): converts the cues of a media's not-yet-converted Simplified
tracks and the original_text of their segments in place (ids, boundaries, review state and corrected_text are
kept), in one transaction, and writes one audit_log row per media. Re-running is a no-op for converted tracks.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Asset, AuditLog, Cue, Media, Segment, Setting, Track, utcnow
from .queue import JobContext, JobError, handler

log = logging.getLogger("voti.textconv")

FLAG = "s2t_converted"
DEFAULTS = {"convert_simplified": True, "opencc_config": "s2twp"}
CONFIGS = ("s2twp", "s2tw", "s2t")

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ffff]")
_SIMPLIFIED_REGIONS = {"cn", "sg", "my"}
_TRADITIONAL_REGIONS = {"tw", "hk", "mo"}
MIN_HAN = 6            # fewer Han characters than this: undecidable → not converted
MIN_SIMPLIFIED = 2     # at least this many characters must be Simplified-only …
SIMPLIFIED_SHARE = 0.01  # … and at least this share of the Han characters


# ───────────────────────────── settings / OpenCC ─────────────────────────────

def text_settings(s: Session) -> dict:
    row = s.get(Setting, "text")
    out = dict(DEFAULTS)
    if row is not None and isinstance(row.value, dict):
        out.update({k: v for k, v in row.value.items() if k in DEFAULTS and v is not None})
    if out["opencc_config"] not in CONFIGS:
        out["opencc_config"] = DEFAULTS["opencc_config"]
    return out


@lru_cache
def converter(config: str):  # noqa: ANN201
    import opencc
    return opencc.OpenCC(config)


@lru_cache
def opencc_version() -> str:
    try:
        from importlib.metadata import version
        return version("opencc")
    except Exception:  # noqa: BLE001
        return "?"


def convert(text: str, config: str = "s2twp") -> str:
    return converter(config).convert(text) if text else text


# ───────────────────────────── detection ─────────────────────────────

def track_script(language: str | None) -> str | None:
    """By language tag: "simplified" | "traditional" | "detect" (Chinese, script unknown) | None (not Chinese)."""
    parts = [p for p in re.split(r"[-_]", (language or "").strip().lower()) if p]
    if not parts or parts[0] not in ("zh", "cmn"):
        return None
    rest = parts[1:]
    if "hans" in rest:
        return "simplified"
    if "hant" in rest:
        return "traditional"
    if rest and rest[0] in _SIMPLIFIED_REGIONS:
        return "simplified"
    if rest and rest[0] in _TRADITIONAL_REGIONS:
        return "traditional"
    return "detect"


def _changed_chars(a: str, b: str) -> int:
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def detect_script(texts: list[str]) -> dict:
    """Content-based script detection. Characters that s2t changes are Simplified-only, characters that t2s
    changes are Traditional-only; text identical in both scripts counts for neither.
    → {"script": simplified|traditional|neutral|mixed|unknown, "han": n, "simplified_chars": n, "traditional_chars": n}"""
    han = "".join(_HAN.findall("".join(t or "" for t in texts)))
    out = {"han": len(han), "simplified_chars": 0, "traditional_chars": 0}
    if len(han) < MIN_HAN:
        return {"script": "unknown", **out}
    simp = _changed_chars(han, converter("s2t").convert(han))
    trad = _changed_chars(han, converter("t2s").convert(han))
    out.update(simplified_chars=simp, traditional_chars=trad)
    if simp == 0 and trad == 0:
        script = "neutral"
    elif simp >= max(MIN_SIMPLIFIED, SIMPLIFIED_SHARE * len(han)) and simp > 3 * trad:
        script = "simplified"
    elif trad > 3 * simp:
        script = "traditional"
    else:
        script = "mixed"
    return {"script": script, **out}


def decide(language: str | None, texts: list[str]) -> dict:
    """→ {"script", "source": "language"|"content"|None, "convert": bool, …detection stats}"""
    by_lang = track_script(language)
    if by_lang is None:
        return {"script": "not_chinese", "source": "language", "convert": False}
    if by_lang in ("simplified", "traditional"):
        return {"script": by_lang, "source": "language", "convert": by_lang == "simplified"}
    d = detect_script(texts)
    return {**d, "source": "content", "convert": d["script"] == "simplified"}


def convertible(language: str | None, s2t_info: dict | None) -> bool:
    """Could a `media.convert_text` job change this track? (cheap; no cue access)"""
    script = track_script(language)
    if script is None or script == "traditional":
        return False
    if not s2t_info:
        return True  # never examined (ingested before conversion existed)
    if s2t_info.get("converted"):
        return False
    return s2t_info.get("script") in ("simplified",)


# ───────────────────────────── ingestion ─────────────────────────────

def convert_parsed_cues(cues: list, language: str | None, settings: dict) -> dict:
    """Convert subtitles.ParsedCue objects in place when the track is Simplified. Returns the record stored in
    the subtitle asset's params["s2t"]."""
    d = decide(language, [c.text for c in cues])
    info = {k: v for k, v in d.items() if k != "convert"}
    info.update(converted=False, config=settings["opencc_config"], opencc=opencc_version())
    if not d["convert"]:
        return info
    if not settings.get("convert_simplified", True):
        info["reason"] = "disabled"
        return info
    conv = converter(settings["opencc_config"])
    n = 0
    for c in cues:
        new = conv.convert(c.text) if c.text else c.text
        if new != c.text:
            c.text = new
            if FLAG not in c.flags:
                c.flags.append(FLAG)
            n += 1
    info.update(converted=True, cues_converted=n, at=utcnow().isoformat())
    return info


def media_summary(tracks: list[dict]) -> dict:
    """Media.metadata["text_conversion"] from per-track records [{track_id, language, kind, **s2t}]."""
    conv = [t for t in tracks if t.get("converted")]
    return {"converted": bool(conv), "tracks": tracks,
            "cues_converted": sum(t.get("cues_converted", 0) for t in conv),
            "segments_converted": sum(t.get("segments_converted", 0) for t in conv),
            "config": conv[0]["config"] if conv else None, "opencc": opencc_version(), "at": utcnow().isoformat()}


# ───────────────────────────── retroactive job ─────────────────────────────

def convert_media(s: Session, media_id: uuid.UUID, settings: dict, user_id: uuid.UUID | None = None) -> dict:
    """Convert one media's Simplified tracks + their segments inside the caller's transaction."""
    media = s.get(Media, media_id)
    if media is None:
        raise JobError("not_found", "找不到媒體", retryable=False)
    cfg = settings["opencc_config"]
    conv = converter(cfg)
    records, totals = [], {"tracks": 0, "tracks_converted": 0, "already": 0, "cues": 0, "segments": 0,
                           "segments_with_correction": 0}
    for tr in s.scalars(select(Track).where(Track.media_id == media_id).order_by(Track.created_at)):
        totals["tracks"] += 1
        asset = s.get(Asset, tr.asset_id) if tr.asset_id else None
        prev = dict((asset.params or {}).get("s2t") or {}) if asset else {}
        if prev.get("converted"):
            totals["already"] += 1
            records.append({"track_id": str(tr.id), "language": tr.language, "kind": tr.kind, **prev})
            continue
        cues = s.scalars(select(Cue).where(Cue.track_id == tr.id).order_by(Cue.idx)).all()
        d = decide(tr.language, [c.text for c in cues])
        info = {k: v for k, v in d.items() if k != "convert"}
        info.update(converted=False, config=cfg, opencc=opencc_version())
        if d["convert"]:
            n_cues = 0
            for c in cues:
                if FLAG in (c.flags or []) or not c.text:
                    continue
                new = conv.convert(c.text)
                if new != c.text:
                    c.text, c.flags = new, [*(c.flags or []), FLAG]
                    n_cues += 1
            n_segs = n_corr = 0
            for seg in s.scalars(select(Segment).where(Segment.media_id == media_id, Segment.track_id == tr.id)):
                if FLAG in (seg.flags or []) or not seg.original_text:
                    continue
                new = conv.convert(seg.original_text)
                if new != seg.original_text:
                    # corrected_text (human), review state, boundaries and revision are left untouched
                    seg.original_text, seg.flags = new, sorted({*(seg.flags or []), FLAG})
                    n_segs += 1
                    n_corr += seg.corrected_text is not None
            info.update(converted=True, cues_converted=n_cues, segments_converted=n_segs, retroactive=True,
                        at=utcnow().isoformat())
            totals["tracks_converted"] += 1
            totals["cues"] += n_cues
            totals["segments"] += n_segs
            totals["segments_with_correction"] += n_corr
        if asset is not None:
            asset.params = {**(asset.params or {}), "s2t": info}
        records.append({"track_id": str(tr.id), "language": tr.language, "kind": tr.kind, **info})
    media.metadata_ = {**(media.metadata_ or {}), "text_conversion": media_summary(records)}
    result = {"media_id": str(media_id), "config": cfg, "opencc": opencc_version(), **totals}
    s.add(AuditLog(user_id=user_id, action="media.convert_text", entity_type="media", entity_id=str(media_id),
                   before=None, after=result))
    return result


@handler("media.convert_text")
def h_convert_text(ctx: JobContext) -> dict:
    from .db import session_scope
    if ctx.media_id is None:
        raise JobError("bad_job", "缺少 media_id", retryable=False)
    with session_scope() as s:
        settings = text_settings(s)
    if not settings["convert_simplified"]:
        return {"skipped": True, "reason": "disabled"}
    ctx.progress(0.1, "convert_text", f"OpenCC {settings['opencc_config']}", force=True)
    with session_scope() as s:  # one transaction: a crash leaves nothing half-converted
        res = convert_media(s, ctx.media_id, settings, ctx.created_by)
    ctx.progress(1.0, "done", f"轉換 {res['cues']} 句字幕、{res['segments']} 個音段", force=True)
    return res


def track_infos(s: Session, tracks: list[Track]) -> dict[uuid.UUID, dict]:
    """track id → stored s2t record (from the subtitle asset params), for API output."""
    ids = [t.asset_id for t in tracks if t.asset_id]
    if not ids:
        return {}
    params = dict(s.execute(select(Asset.id, Asset.params).where(Asset.id.in_(ids))).all())
    return {t.id: (params.get(t.asset_id) or {}).get("s2t") or {} for t in tracks if t.asset_id}


def convertible_media(s: Session, *conds) -> set[uuid.UUID]:  # noqa: ANN002
    """Media (filtered by `conds` on Media) that have at least one track a convert_text job could change."""
    rows = s.execute(select(Track.media_id, Track.language, Asset.params)
                     .join(Media, Media.id == Track.media_id)
                     .outerjoin(Asset, Asset.id == Track.asset_id).where(*conds)).all()
    return {r.media_id for r in rows if convertible(r.language, (r.params or {}).get("s2t"))}
