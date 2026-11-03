"""`media.validate`: does a segment's caption text belong to its audio?

Captions are sometimes a translation of the speech (a Mandarin news video that inserts English interview clips
with Chinese subtitles, a whole video subtitled in another language) or unrelated to it. Such segments are marked
`validity = "invalid"`: they stay playable, but are excluded from dataset previews/exports, hour totals, speaker
embedding/profiling/clustering and diversity statistics.

Signals per active segment (docs/ARCHITECTURE.md "Caption validation"):
  * spoken-language ID — SpeechBrain `speechbrain/lang-id-voxlingua107-ecapa` (VoxLingua107, Apache-2.0, ~80 MB in
    MODEL_CACHE/speechbrain/lang-id-voxlingua107-ecapa) over the segment's speech frames (Brouhaha VAD; whole span
    when no frame data), at most LID_MAX_SEC; skipped below `lid_min_speech_sec` of speech. Top-3 kept.
  * alignment confidence — mean MMS_FA probability of the timed units of the segment's current, valid
    `word_alignments` row (voti.pipeline.align; Han characters / Latin words, star units excluded). Only for
    alignable languages (zh*, cmn, en); null for nan*/hak* (no romanizer) and everything else.

Decision (settings `validation`, see DEFAULTS):
  * no text (text_source none / blank text)                    → unknown (never invalid)
  * caption family zh/en/ja/…: LID top ≠ family, prob ≥ lid_min_prob     → invalid, language_mismatch
  * caption nan*/hak*/yue (no VoxLingua107 class; Taiwanese is often heard as zh, vi, …): only a confident LID
    top in CLEARLY_DIFFERENT (en, ja, ko, fr, de, …)            → invalid, language_mismatch
  * otherwise align_score < align_min_score (enough timed units)          → invalid, text_mismatch
  * otherwise valid when at least one signal was available, else unknown — except OCR text (text_source
    ocr_caption, voti.pipeline.ocr): without a usable alignment score it stays unknown (note ocr_unverified).
Manual decisions (validity_source = "manual", PATCH /segments/{id}) are never overwritten.

A segment's result is current while `validation.revision` / `validation.text_hash` match the segment (and, when it
used the alignment, the alignment is still the one it saw); the job only processes segments without a current
result unless payload force=true. Payload: {} | {"segment_ids": [...]} | {"force": true} | {"chain": false}.
GPU when available; CUDA OOM → voti.pipeline.gpu policy; CUDA cache emptied after the job.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import select

from ..config import get_config
from ..db import session_scope
from ..models import Segment, WordAlignment, utcnow
from ..queue import JobError
from .common import SR, get_setting, load_frames, load_normalized, work_dir

log = logging.getLogger("voti.pipeline.validate")

LID_MODEL_ID = "speechbrain/lang-id-voxlingua107-ecapa"
LID_MAX_SEC = 20.0               # longest speech fed to the classifier per segment
BATCH_SAMPLES = SR * 200         # padded samples per forward pass
VERSION = 1                      # bump when the decision logic changes (results are then recomputed)
# (the ocr_caption rule only affects the new text source, so VERSION stays 1)

DEFAULTS: dict = {
    "enabled": True,
    "lid_min_prob": 0.7,         # a different spoken language must be at least this probable
    "lid_min_speech_sec": 1.0,   # less speech than this: no language ID
    "align_min_score": 0.4,      # mean MMS_FA unit probability below this: text does not match (calibrated)
    "align_min_units": 4,        # fewer timed units: alignment score not used
}

# VoxLingua107 classes a Taiwanese / Hakka / Cantonese track is never "confused" with — a confident one of these
# means the speech really is another language (Sinitic-adjacent confusions such as zh, vi, th, lo, km, my, ko?
# are deliberately absent except for the clearly different ones listed here).
CLEARLY_DIFFERENT = frozenset({"en", "ja", "ko", "fr", "de", "es", "it", "pt", "ru", "nl", "pl", "uk", "tr", "ar",
                               "hi", "sv", "id"})
LENIENT_BASES = frozenset({"nan", "hak", "yue", "tw"})
# caption language base → VoxLingua107 code
LID_CODE = {"cmn": "zh", "he": "iw", "jv": "jw", "nb": "no", "fil": "tl", "zh": "zh"}

_lock = threading.Lock()


def base_language(language: str | None) -> str:
    return (language or "").lower().replace("_", "-").split("-")[0]


def expected_language(language: str | None, text: str = "") -> tuple[str | None, bool]:
    """Caption language → (expected VoxLingua107 code | None, lenient). lenient: nan/hak/yue tracks (no class)."""
    from .align import HAN
    base = base_language(language)
    if base in LENIENT_BASES:
        return None, True
    if not base:
        return ("zh", False) if HAN.search(text or "") else (None, False)
    return LID_CODE.get(base, base), False


CJK = frozenset({"zh", "ja", "ko"})  # VoxLingua107 codes of Han / kana / hangul text
MIXED_LATIN_SHARE = 0.3              # Latin words / (Han characters + Latin words) for a code-switched sentence


def text_script(text: str) -> str:
    """"latin" (Latin words, no Han), "mixed" (Han with ≥ MIXED_LATIN_SHARE Latin words), "han" or "other"."""
    from .align import HAN, LATIN_WORD
    han = len(HAN.findall(text or ""))
    words = len(LATIN_WORD.findall(text or ""))
    if not han:
        return "latin" if words >= 2 or sum(len(w) for w in LATIN_WORD.findall(text or "")) >= 4 else "other"
    return "mixed" if words / (han + words) >= MIXED_LATIN_SHARE else "han"


def settings_value(raw: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (raw or {}).items() if k in DEFAULTS and v is not None})
    return out


# ───────────────────────────── alignment confidence ─────────────────────────────

def align_confidence(units: Sequence[Sequence] | None) -> tuple[float | None, int, float | None]:
    """[[unit, start, end, score], ...] → (mean score of the scored units, number of scored units, fraction of
    scored units below 0.05). Star units / untimed units have score null and are ignored."""
    scores = [float(u[3]) for u in (units or []) if len(u) > 3 and u[3] is not None]
    if not scores:
        return None, 0, None
    arr = np.asarray(scores, dtype=np.float64)
    return float(arr.mean()), int(arr.size), float((arr < 0.05).mean())


# ───────────────────────────── decision (pure) ─────────────────────────────

@dataclass
class Verdict:
    validity: str                       # valid | invalid | unknown
    reason: str | None = None           # language_mismatch | text_mismatch | None
    details: dict = field(default_factory=dict)


def decide(*, text: str, text_source: str | None, language: str | None, lid: list[tuple[str, float]] | None,
           align_score: float | None, align_units: int, cfg: dict) -> Verdict:
    """lid: top-k [(code, prob)] or None (not run); align_score: mean unit probability or None."""
    cfg = settings_value(cfg)
    details: dict = {}
    if text_source == "none" or not (text or "").strip():
        return Verdict("unknown", None, {"note": "no_text"})
    expected, lenient = expected_language(language, text)
    details["expected"] = expected or ("sinitic" if lenient else None)
    script = text_script(text)
    if script == "latin" and (lenient or expected in CJK):
        # an English sentence inside a Chinese / Taiwanese track: the text, not the track, says what is spoken —
        # only a confident CJK language ID then means a mismatch (e.g. Chinese speech with an English subtitle)
        expected, lenient = None, False
        details["expected"] = "latin"
    if lid:
        top, prob = lid[0]
        mismatch = False
        if details["expected"] == "latin":
            mismatch = top in CJK and prob >= cfg["lid_min_prob"]
        elif lenient:
            mismatch = top in CLEARLY_DIFFERENT and prob >= cfg["lid_min_prob"]
        elif expected is not None:
            mismatch = top != expected and prob >= cfg["lid_min_prob"]
            if mismatch and script == "mixed" and expected in CJK and top == "en":
                mismatch = False  # code-switched sentence (lots of English words): English speech is plausible
        if mismatch:
            return Verdict("invalid", "language_mismatch", details)
    if align_score is not None and align_units >= cfg["align_min_units"] and not lenient:
        if align_score < cfg["align_min_score"]:
            return Verdict("invalid", "text_mismatch", details)
        return Verdict("valid", None, details)
    if text_source == "ocr_caption":
        # OCR text is only trusted with a usable alignment score (a language ID alone accepts ~10 % of swapped
        # texts); without one the segment stays unknown — datasets take ocr_caption segments only when valid
        details["note"] = "ocr_unverified"
        return Verdict("unknown", None, details)
    if lid:
        return Verdict("valid", None, details)
    details["note"] = "no_signal"
    return Verdict("unknown", None, details)


# ───────────────────────────── language ID model ─────────────────────────────

def model_dir() -> Path:
    return Path(get_config().model_cache) / "speechbrain" / "lang-id-voxlingua107-ecapa"


@lru_cache(maxsize=2)
def load_lid(device: str):
    try:
        from speechbrain.inference.classifiers import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy
    except ImportError as e:
        raise JobError("validate_unavailable", f"此 worker 未安裝 speechbrain：{e}", blocked=True) from e
    try:
        m = EncoderClassifier.from_hparams(source=LID_MODEL_ID, savedir=str(model_dir()), run_opts={"device": device},
                                           local_strategy=LocalStrategy.COPY)
    except Exception as e:  # noqa: BLE001
        from ..queue import is_transient
        from .gpu import is_cuda_oom
        if is_cuda_oom(e):
            raise
        retry = is_transient(e) or isinstance(e, OSError)
        raise JobError("lid_model_unavailable", f"VoxLingua107 語言辨識模型下載或載入失敗：{str(e)[:300]}",
                       retryable=retry, blocked=not retry) from e
    m.eval()
    labels = m.hparams.label_encoder.ind2lab
    m.voti_codes = [str(labels[i]).split(":", 1)[0].strip() for i in range(len(labels))]
    return m


def lid_batch(clips: list[np.ndarray], device: str, top_k: int = 3) -> list[list[tuple[str, float]]]:
    """16 kHz float32 clips → per clip [(code, prob)] top-k (softmax over the 107 classes)."""
    import torch
    if not clips:
        return []
    m = load_lid(device)
    dev = m.device if hasattr(m, "device") else device
    order = np.argsort([c.size for c in clips])
    out: list = [None] * len(clips)
    i = 0
    with _lock, torch.inference_mode():
        while i < len(order):
            j = i + 1
            while j < len(order) and clips[order[j]].size * (j - i + 1) <= BATCH_SAMPLES and j - i < 32:
                j += 1
            idx = order[i:j]
            longest = max(clips[k].size for k in idx)
            wav = np.zeros((len(idx), longest), dtype=np.float32)
            lens = np.zeros(len(idx), dtype=np.float32)
            for r, k in enumerate(idx):
                wav[r, :clips[k].size] = clips[k]
                lens[r] = clips[k].size / longest
            logp = m.classify_batch(torch.from_numpy(wav).to(dev), torch.from_numpy(lens).to(dev))[0]
            probs = torch.softmax(logp.float(), dim=-1).cpu().numpy()  # log-softmax output → renormalised probs
            for r, k in enumerate(idx):
                top = np.argsort(-probs[r])[:top_k]
                out[k] = [(m.voti_codes[t], round(float(probs[r, t]), 4)) for t in top]
            i = j
    return out


def speech_clip(audio: np.ndarray, start: float, end: float, frames) -> np.ndarray:  # noqa: ANN001
    """Speech samples of [start, end) (Brouhaha VAD ≥ 0.5), the whole span without frame data; ≤ LID_MAX_SEC."""
    if frames is None:
        clip = audio[int(start * SR):int(end * SR)]
    else:
        from .embed import speech_clip as vad_clip
        clip = vad_clip(audio, start, end, frames, None)
    return clip[: int(LID_MAX_SEC * SR)]


# ───────────────────────────── job ─────────────────────────────

def _alignment_stamp(g: Segment, wa: WordAlignment | None) -> str | None:
    """Identity of the alignment a validation may use: the valid `aligned` row's timestamp, else None."""
    from .align import _valid
    return wa.created_at.isoformat() if (wa is not None and wa.status == "aligned" and _valid(wa, g)) else None


def _is_current(g: Segment, wa: WordAlignment | None) -> bool:
    from .align import segment_text, text_hash
    v = g.validation or {}
    return (v.get("version") == VERSION and v.get("revision") == g.revision
            and v.get("text_hash") == text_hash(segment_text(g)) and v.get("alignment_at") == _alignment_stamp(g, wa))


@dataclass
class Todo:
    id: uuid.UUID
    revision: int
    start: float
    end: float
    text: str
    text_source: str
    language: str | None
    units: list | None
    alignment_at: str | None
    lid: list | None = None        # reusable language ID of the same revision (audio unchanged)

    @property
    def key(self) -> tuple:
        from .align import text_hash
        return self.id, self.revision, text_hash(self.text), self.alignment_at


def select_todo(media_id: uuid.UUID, segment_ids: Sequence[str] | None = None, force: bool = False,
                done: set | None = None) -> list[Todo]:
    """Non-manual active segments without a current validation (+ segment_ids; all with force). `done`: keys
    already handled by this job (a re-scan only picks up what changed meanwhile)."""
    from .align import segment_text
    want = {uuid.UUID(str(x)) for x in (segment_ids or [])}
    with session_scope() as s:
        segs = s.scalars(select(Segment).where(Segment.media_id == media_id, Segment.active.is_(True))
                         .order_by(Segment.start_sec)).all()
        rows = {r.segment_id: r for r in s.scalars(select(WordAlignment).where(WordAlignment.media_id == media_id))}
        out = []
        for g in segs:
            if g.validity_source == "manual":
                continue
            wa = rows.get(g.id)
            if not (force or g.id in want or not _is_current(g, wa)):
                continue
            stamp = _alignment_stamp(g, wa)
            v = g.validation or {}
            lid = v.get("lid") if (not force and v.get("revision") == g.revision
                                   and v.get("lid_model") == LID_MODEL_ID) else None
            t = Todo(g.id, g.revision or 1, g.start_sec, g.end_sec, segment_text(g), g.text_source, g.language,
                     wa.units if stamp else None, stamp, [tuple(x) for x in lid] if lid else None)
            if done is None or t.key not in done:
                out.append(t)
        return out


def _write(rows: list[dict]) -> dict:
    """Apply verdicts; skips segments that became manual or changed (revision / text) meanwhile.
    → {"changed_to_valid": n} (segments that were invalid and no longer are)."""
    from .align import segment_text, text_hash
    changed = 0
    with session_scope() as s:
        segs = {g.id: g for g in s.scalars(select(Segment).where(Segment.id.in_([r["id"] for r in rows]))
                                           .with_for_update())}
        for r in rows:
            g = segs.get(r["id"])
            if g is None or g.validity_source == "manual" or (g.revision or 1) != r["revision"] or \
                    text_hash(segment_text(g)) != r["validation"]["text_hash"]:
                continue
            if g.validity == "invalid" and r["validity"] != "invalid":
                changed += 1
            g.validity, g.invalid_reason = r["validity"], r["reason"]
            g.validity_source = "auto"
            g.lid_lang, g.lid_prob, g.align_score = r["lid_lang"], r["lid_prob"], r["align_score"]
            g.validation = r["validation"]
    return {"changed_to_valid": changed}


def _verdict_row(t: Todo, lid: list | None, cfg: dict, now: str) -> tuple[dict, Verdict]:
    from .align import text_hash
    score, n_units, low_frac = align_confidence(t.units)
    v = decide(text=t.text, text_source=t.text_source, language=t.language, lid=lid, align_score=score,
               align_units=n_units, cfg=cfg)
    row = {"id": t.id, "revision": t.revision, "validity": v.validity, "reason": v.reason,
           "lid_lang": lid[0][0] if lid else None, "lid_prob": lid[0][1] if lid else None,
           "align_score": None if score is None else round(score, 4),
           "validation": {**v.details, "version": VERSION, "revision": t.revision, "text_hash": text_hash(t.text),
                          "alignment_at": t.alignment_at, "lid": [list(x) for x in lid] if lid else None,
                          "lid_model": LID_MODEL_ID, "align_units": n_units,
                          "align_low_fraction": None if low_frac is None else round(low_frac, 4),
                          "thresholds": {k: cfg[k] for k in ("lid_min_prob", "align_min_score", "align_min_units",
                                                             "lid_min_speech_sec")},
                          "at": now}}
    return row, v


def run(media_id: uuid.UUID, progress: Callable[[float, str], None] = lambda f, p: None,
        segment_ids: Sequence[str] | None = None, force: bool = False) -> dict:
    from .gpu import OomFallback, free_cuda
    t0 = time.monotonic()
    with session_scope() as s:
        cfg = settings_value(get_setting(s, "validation"))
    if not cfg["enabled"]:
        return {"skipped": True, "code": "validation_disabled"}
    stats: dict = {"segments": 0, "valid": 0, "invalid": 0, "unknown": 0, "reasons": {}, "lid_run": 0,
                   "lid_reused": 0, "changed_to_valid": 0}
    fallback: OomFallback | None = None
    done: set = set()
    rounds = 0
    try:
        with work_dir("validate") as wd:
            audio = frames = None
            todo = select_todo(media_id, segment_ids, force)
            while todo and rounds < 4:
                rounds += 1
                lid_results: dict = {}
                need = []
                for t in todo:
                    done.add(t.key)
                    if not (t.text or "").strip() or t.text_source == "none":
                        continue
                    if t.lid:
                        lid_results[t.id] = t.lid
                        stats["lid_reused"] += 1
                    else:
                        need.append(t)
                if need:
                    if audio is None:
                        with session_scope() as s:
                            audio = load_normalized(s, media_id, wd)
                            frames = load_frames(s, media_id)
                    clips, ids = [], []
                    for t in need:
                        clip = speech_clip(audio, t.start, t.end, frames)
                        if clip.size >= cfg["lid_min_speech_sec"] * SR:
                            clips.append(clip)
                            ids.append(t.id)
                    if clips:
                        if fallback is None:
                            progress(0.05, "load_model")
                            fallback = OomFallback("VoxLingua107 語言辨識")
                            fallback.call(load_lid)  # download / load once (blocked/retryable errors surface here)
                        for k in range(0, len(clips), 64):  # chunks: progress + cancellation between them
                            part = fallback.call(lambda dev: lid_batch(clips[k:k + 64], dev))
                            lid_results.update(zip(ids[k:k + 64], part))
                            progress(0.1 + 0.8 * min(1.0, (k + len(part)) / len(clips)), "lid")
                        stats["lid_run"] += len(clips)
                now = utcnow().isoformat()
                rows = []
                for t in todo:
                    row, v = _verdict_row(t, lid_results.get(t.id), cfg, now)
                    rows.append(row)
                    stats["segments"] += 1
                    stats[v.validity] += 1
                    if v.reason:
                        stats["reasons"][v.reason] = stats["reasons"].get(v.reason, 0) + 1
                stats["changed_to_valid"] += _write(rows)["changed_to_valid"]
                # edits / re-alignments that landed while we were running are picked up here
                todo = select_todo(media_id, None, False, done)
    finally:
        free_cuda()
    stats.update(rounds=rounds, device=fallback.device if fallback else None,
                 cpu_fallback=bool(fallback and fallback.fell_back), model=LID_MODEL_ID,
                 elapsed_sec=round(time.monotonic() - t0, 2))
    return stats
