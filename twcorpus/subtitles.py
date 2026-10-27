"""SRT/WebVTT parsing preserves raw cues and flags malformed timings."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

TIMING = re.compile(r"(?P<start>(?:-?\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<end>(?:-?\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})(?:\s+.*)?$")
TAG = re.compile(r"<[^>]*>")


@dataclass
class Cue:
    ordinal: int
    original_id: str
    start_sec: float | None
    end_sec: float | None
    raw_text: str
    cleaned_text: str
    flags: list[str] = field(default_factory=list)


def timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) not in (2, 3):
        raise ValueError("時間欄位格式無效")
    sign = -1 if parts[0].startswith("-") else 1
    numbers = [abs(int(parts[0])), int(parts[1]), float(parts[2])] if len(parts) == 3 else [0, abs(int(parts[0])), float(parts[1])]
    if not 0 <= numbers[1] < 60 or not 0 <= numbers[2] < 60:
        raise ValueError("時間欄位超出範圍")
    return sign * (numbers[0] * 3600 + numbers[1] * 60 + numbers[2])


def clean_text(value: str) -> str:
    # Strip styling only. Preserve spoken repetitions, digits, English, and dialect text.
    value = TAG.sub("", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_text(text: str, duration_sec: float | None = None) -> list[Cue]:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^WEBVTT[^\n]*\n", "", text)
    cues = []
    previous: Cue | None = None
    for block in re.split(r"\n\s*\n", text):
        lines = [s.strip() for s in block.splitlines() if s.strip()]
        if not lines or lines[0].startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = next((i for i, line in enumerate(lines[:3]) if "-->" in line), None)
        if timing_index is None:
            continue
        original_id = lines[timing_index - 1] if timing_index else str(len(cues) + 1)
        raw = "\n".join(lines[timing_index + 1:])
        cleaned = clean_text(raw)
        flags = []
        match = TIMING.fullmatch(lines[timing_index])
        start = end = None
        if not match:
            flags.append("invalid_timestamp")
        else:
            try:
                start, end = timestamp(match["start"]), timestamp(match["end"])
            except ValueError:
                flags.append("invalid_timestamp")
        if start is not None and end is not None:
            if start < 0:
                flags.append("negative_start")
            if end <= start:
                flags.append("non_positive_duration")
            if duration_sec is not None and end > duration_sec + 0.02:
                flags.append("beyond_audio")
            if previous and previous.end_sec is not None:
                if start < previous.end_sec:
                    flags.append("overlap")
                if start == previous.start_sec and end == previous.end_sec and cleaned == previous.cleaned_text:
                    flags.append("duplicate_cue")
                if start < previous.end_sec and (cleaned.startswith(previous.cleaned_text) or previous.cleaned_text.startswith(cleaned)) and cleaned != previous.cleaned_text:
                    flags.append("possible_rolling_caption")
        if not cleaned:
            flags.append("empty_text")
        cue = Cue(len(cues) + 1, original_id, start, end, raw, cleaned, flags)
        cues.append(cue)
        previous = cue
    return cues


def parse_file(path: Path, duration_sec: float | None = None) -> list[Cue]:
    if path.suffix.lower() not in (".srt", ".vtt"):
        raise ValueError("字幕須為 SRT 或 VTT")
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp950"):
        try:
            return parse_text(data.decode(encoding), duration_sec)
        except UnicodeError:
            continue
    raise ValueError("字幕編碼無法辨識；請轉成 UTF-8")


def group_cues(cues: list[Cue], duration_sec: float, padding: float = 0.12,
               max_gap: float = 0.25, target_sec: float = 8.0) -> list[dict]:
    """Conservative contiguous grouping; never split a cue or silently remove anomalies."""
    valid = [c for c in cues if c.start_sec is not None and c.end_sec is not None and
             c.start_sec >= 0 and c.end_sec > c.start_sec and c.end_sec <= duration_sec + 0.02 and c.cleaned_text]
    groups: list[list[Cue]] = []
    for cue in valid:
        if (groups and cue.start_sec >= groups[-1][-1].end_sec and
            cue.start_sec - groups[-1][-1].end_sec <= max_gap and
            cue.end_sec - groups[-1][0].start_sec <= target_sec):
            groups[-1].append(cue)
        else:
            groups.append([cue])
    result = []
    for i, group in enumerate(groups):
        # Do not pad into neighboring cue times. Overlapping subtitles retain original intervals.
        previous_end = groups[i - 1][-1].end_sec if i else 0
        next_start = groups[i + 1][0].start_sec if i + 1 < len(groups) else duration_sec
        start = max(0, group[0].start_sec - padding)
        end = min(duration_sec, group[-1].end_sec + padding)
        if previous_end <= group[0].start_sec:
            start = max(start, min(duration_sec, previous_end))
        else:
            start = group[0].start_sec
        if next_start >= group[-1].end_sec:
            end = min(end, next_start)
        else:
            end = min(duration_sec, group[-1].end_sec)
        result.append({"cues": group, "start_sec": start, "end_sec": end,
                       "text": " ".join(c.cleaned_text for c in group)})
    return result
