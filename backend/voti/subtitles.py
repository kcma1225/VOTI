"""Subtitle parsing and cleaning: WebVTT, SRT and YouTube json3 → cues with cleaned text and flags.

    cues = parse_subtitle(data, fmt="json3", auto=False, duration_sec=123.4)
    → list[ParsedCue(idx, start_sec, end_sec, raw_text, text, flags)]

`raw_text` keeps what the file said (markup stripped of nothing); `text` is the cleaned form used for segments.
Cleaning only removes presentation artefacts: markup, inline timestamps, music notes, bracketed sound
annotations ([音樂], (掌聲), [Music]), dialogue dashes, YouTube `>>` speaker markers, zero-width characters and
redundant whitespace. Spoken content (repetitions, digits, Latin script, Tâi-lô) is never altered.

YouTube auto-captions ("rolling" VTT, where each cue repeats the previous line and adds a new one, plus 10 ms
transition cues) are collapsed so every spoken line appears once. Consecutive identical cues that touch are
merged.

Flags per cue:
  empty                 nothing left after cleaning (raw text kept)
  non_speech            cue consisted only of sound annotations / music notes ([音樂], ♪)
  music                 music notes or a music annotation were present
  speaker_change        dialogue dash or `>>` marker: more than one speaker or a new speaker starts
  overlap               starts before the previous cue ends (after rolling cleanup)
  out_of_range          negative start, end beyond media duration, or end <= start
  merged_duplicate      identical adjacent cues were merged into this one
  end_trimmed           (auto captions) display end clipped to the next cue start
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass, field

FORMATS = ("vtt", "srt", "json3")


@dataclass
class ParsedCue:
    idx: int
    start_sec: float
    end_sec: float
    raw_text: str
    text: str
    flags: list[str] = field(default_factory=list)


@dataclass
class _Raw:
    start: float
    end: float
    lines: list[str]          # raw lines (markup intact)
    flags: list[str] = field(default_factory=list)


# ───────────────────────────── format detection & decoding ─────────────────────────────

def decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "utf-16", "cp950"):
        try:
            text = data.decode(enc)
        except UnicodeError:
            continue
        if enc == "utf-16" and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
            continue  # utf-16 without BOM almost never happens for subtitles; avoid mis-decoding
        return text
    return data.decode("utf-8", errors="replace")


def detect_format(text: str) -> str:
    head = text.lstrip("\ufeff \t\r\n")[:200]
    if head.startswith("{"):
        return "json3"
    if head.startswith("WEBVTT"):
        return "vtt"
    return "srt"


# ───────────────────────────── timestamps ─────────────────────────────

_TS = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_TIMING = re.compile(rf"^\s*(?P<start>{_TS})\s*-->\s*(?P<end>{_TS})(?P<settings>\s.*)?$")


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        h, m, s = 0, int(parts[0]), float(parts[1])
    else:
        raise ValueError(f"bad timestamp {value!r}")
    if m >= 60 or s >= 60:
        raise ValueError(f"bad timestamp {value!r}")
    return h * 3600 + m * 60 + s


# ───────────────────────────── parsers (raw cues) ─────────────────────────────

def _blocks(text: str, strict: bool) -> list[list[str]]:
    """Split into blank-line separated blocks. WebVTT (strict): only truly empty lines separate cues —
    YouTube ASR cues contain a whitespace-only first line. SRT in the wild: whitespace-only lines too."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return [b.split("\n") for b in re.split(r"\n\n+" if strict else r"\n[ \t]*\n", text)]


def _parse_timed_blocks(text: str, vtt: bool) -> list[_Raw]:
    out: list[_Raw] = []
    blocks = _blocks(text, strict=vtt)
    for bi, lines in enumerate(blocks):
        # keep inner blank-ish lines out, but keep line structure for rolling detection
        while lines and not lines[0].strip():
            lines = lines[1:]
        if not lines:
            continue
        if vtt and bi == 0 and lines[0].startswith("WEBVTT"):
            continue  # header block (WEBVTT + Kind:/Language: metadata)
        if vtt and lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        ti = next((i for i, ln in enumerate(lines[:3]) if "-->" in ln), None)
        if ti is None:
            # text separated from its timing line by a blank line (whitespace stripped by an editor):
            # attach it to the preceding cue if that one has no text yet
            if out and not any(ln.strip() for ln in out[-1].lines):
                out[-1].lines = [ln.rstrip() for ln in lines]
            continue
        m = _TIMING.match(lines[ti])
        if not m:
            continue
        try:
            start, end = parse_timestamp(m["start"]), parse_timestamp(m["end"])
        except ValueError:
            continue
        body = [ln.rstrip() for ln in lines[ti + 1:]]
        out.append(_Raw(start, end, body))
    return out


def parse_vtt(text: str) -> list[_Raw]:
    return _parse_timed_blocks(text, vtt=True)


def parse_srt(text: str) -> list[_Raw]:
    return _parse_timed_blocks(text, vtt=False)


def parse_json3(text: str) -> list[_Raw]:
    """YouTube timedtext json3. Window-definition events (no segs) and append-newline events are skipped."""
    data = json.loads(text)
    out: list[_Raw] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        raw = "".join(s.get("utf8", "") for s in segs)
        if not raw.strip():  # includes aAppend newline events
            continue
        start = (ev.get("tStartMs") or 0) / 1000.0
        dur = ev.get("dDurationMs")
        end = start + (dur / 1000.0 if dur is not None else 0.0)
        out.append(_Raw(start, end, raw.split("\n")))
    return out


# ───────────────────────────── cleaning ─────────────────────────────

_INLINE_TS = re.compile(r"<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}>")
_ANY_TAG = re.compile(r"<[^<>]{0,200}>")
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]")
_MUSIC = re.compile("[\u266a\u266b\u266c\u2669\U0001F3B5\U0001F3B6]")
# bracketed sound annotations: [音樂] (掌聲) 【笑聲】 [Music] [Applause] （音樂）
_ANNOT_WORDS = (r"音樂|音乐|配樂|背景音樂|掌聲|掌声|笑聲|笑声|笑|歡呼|歡呼聲|鼓掌|嘆氣|噪音|雜音|靜音|無聲|"
                r"music|applause|laughter|laughs|laughing|cheering|cheers|silence|noise|inaudible|"
                r"foreign|no audio|singing|instrumental|bgm")
_ANNOTATION = re.compile(rf"[\[(（【〔]\s*(?:{_ANNOT_WORDS})\s*[\])）】〕]", re.IGNORECASE)
_MUSIC_ANNOT = re.compile(r"[\[(（【〔]\s*(?:音樂|音乐|配樂|背景音樂|music|instrumental|singing|bgm)\s*[\])）】〕]",
                          re.IGNORECASE)
_DASH = re.compile(r"^\s*(?:[-‐‑–—―]|－)\s*")
_SPEAKER_MARK = re.compile(r"^\s*>>+\s*")
_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef\U00020000-\U0002ffff]")


def _strip_markup(line: str) -> str:
    line = _INLINE_TS.sub("", line)
    line = _ANY_TAG.sub("", line)
    return line


def _norm_ws(s: str) -> str:
    s = s.replace("\u3000", " ").replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def _join(lines: list[str]) -> str:
    """Join display lines: no space between two CJK characters, single space otherwise."""
    out = ""
    for ln in lines:
        if not ln:
            continue
        if not out:
            out = ln
        elif _CJK.match(out[-1]) and _CJK.match(ln[0]):
            out += ln
        else:
            out += " " + ln
    return out


def clean_line(line: str) -> tuple[str, set[str]]:
    """Clean one display line. Returns (text, flags)."""
    flags: set[str] = set()
    s = _strip_markup(html.unescape(_strip_markup(line)))  # also catches escaped markup (&lt;i&gt;)
    s = _ZERO_WIDTH.sub("", s)
    s = unicodedata.normalize("NFC", s)
    if _MUSIC.search(s) or _MUSIC_ANNOT.search(s):
        flags.add("music")
    had_annotation = bool(_ANNOTATION.search(s)) or bool(_MUSIC.search(s))
    s = _ANNOTATION.sub(" ", s)
    s = _MUSIC.sub(" ", s)
    if _SPEAKER_MARK.match(s):
        flags.add("speaker_change")
        s = _SPEAKER_MARK.sub("", s)
    if _DASH.match(s) and len(_DASH.sub("", s).strip()) > 0:
        flags.add("speaker_change")
        s = _DASH.sub("", s)
    s = _norm_ws(s)
    if not s and had_annotation:
        flags.add("non_speech")
    return s, flags


def clean_text(raw: str) -> str:
    """Clean a whole (multi-line) cue text."""
    return clean_lines(raw.split("\n"))[0]


def clean_lines(lines: list[str]) -> tuple[str, set[str]]:
    flags: set[str] = set()
    cleaned = []
    for ln in lines:
        c, f = clean_line(ln)
        flags |= f
        if c:
            cleaned.append(c)
    text = _join(cleaned)
    if not text:
        flags.add("empty")
    else:
        flags.discard("non_speech")
    return text, flags


# ───────────────────────────── rolling caption cleanup ─────────────────────────────

_ROLLING_HINT = re.compile(r"<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}><c>")


def is_rolling_vtt(text: str) -> bool:
    """YouTube ASR VTT carries word timestamps (<00:00:01.234><c> word</c>) and repeats lines."""
    return bool(_ROLLING_HINT.search(text))


def _derolled(raws: list[_Raw]) -> list[_Raw]:
    """Collapse YouTube rolling captions: drop ≤50 ms transition cues and lines already shown."""
    out: list[_Raw] = []
    shown: list[str] = []  # cleaned lines emitted so far (recent window)
    for r in raws:
        if r.end - r.start <= 0.05:
            continue
        new_lines = []
        for ln in r.lines:
            c, f = clean_line(ln)
            if not c:
                if "non_speech" in f and not new_lines:  # keep [Music]-only cues (flagged), like json3
                    new_lines.append(ln)
                continue
            if c in shown[-3:]:
                continue
            new_lines.append(ln)
            shown.append(c)
        if not new_lines:
            continue
        out.append(_Raw(r.start, r.end, new_lines, list(r.flags)))
    # the visible end of a rolled line is when the next line appears
    for a, b in zip(out, out[1:]):
        if b.start < a.end:
            a.end = max(a.start, b.start)
    return out


# ───────────────────────────── main entry ─────────────────────────────

def parse_subtitle(data: bytes | str, fmt: str | None = None, *, auto: bool = False,
                   duration_sec: float | None = None) -> list[ParsedCue]:
    text = decode(data)
    fmt = (fmt or detect_format(text)).lower()
    if fmt in ("json", "json3", "srv3j"):
        raws = parse_json3(text)
        fmt = "json3"
    elif fmt in ("vtt", "webvtt"):
        raws = parse_vtt(text)
        if auto or is_rolling_vtt(text):
            raws = _derolled(raws)
    elif fmt == "srt":
        raws = parse_srt(text)
        if is_rolling_vtt(text):
            raws = _derolled(raws)
    else:
        raise ValueError(f"unsupported subtitle format {fmt!r}")

    raws.sort(key=lambda r: (r.start, r.end))

    # auto captions: display duration overlaps the next line; the next line's start is a better end
    if auto:
        for a, b in zip(raws, raws[1:]):
            if a.start < b.start < a.end:
                a.end = b.start
                a.flags.append("end_trimmed")

    cues: list[ParsedCue] = []
    for r in raws:
        raw_text = "\n".join(ln for ln in r.lines).strip("\n")
        text_c, flags = clean_lines(r.lines)
        prev = cues[-1] if cues else None
        # merge identical neighbours that touch (duplicated cues, re-sent rolling lines)
        if prev and text_c and text_c == prev.text and r.start <= prev.end_sec + 0.05:
            prev.end_sec = max(prev.end_sec, r.end)
            if "merged_duplicate" not in prev.flags:
                prev.flags.append("merged_duplicate")
            continue
        all_flags = list(r.flags) + sorted(flags)
        cues.append(ParsedCue(idx=len(cues), start_sec=round(r.start, 3), end_sec=round(r.end, 3),
                              raw_text=raw_text, text=text_c, flags=all_flags))

    for i, c in enumerate(cues):
        if c.start_sec < 0 or c.end_sec <= c.start_sec or (
                duration_sec is not None and c.end_sec > duration_sec + 0.5):
            _add(c, "out_of_range")
        if i and c.start_sec < cues[i - 1].end_sec - 0.01:
            _add(c, "overlap")
        c.end_sec = round(c.end_sec, 3)
    return cues


def _add(c: ParsedCue, flag: str) -> None:
    if flag not in c.flags:
        c.flags.append(flag)


def summary(cues: list[ParsedCue]) -> dict:
    """Small stats dict for Asset.params / job results."""
    usable = [c for c in cues if c.text and "out_of_range" not in c.flags]
    return {"cues": len(cues), "usable": len(usable),
            "text_sec": round(sum(c.end_sec - c.start_sec for c in usable), 2),
            "chars": sum(len(c.text) for c in usable),
            "flags": {f: sum(f in c.flags for c in cues) for f in sorted({f for c in cues for f in c.flags})}}
