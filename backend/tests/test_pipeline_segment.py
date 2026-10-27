"""Segmentation planning: cue grouping, VAD snapping, splitting, no-caption plans, speaker state, overlaps."""
from __future__ import annotations

import _pipeline_support as sup
import numpy as np

from voti.pipeline import diarize, segment as sg

HOP = 0.016875


def _vad(regions, duration):
    return sg.Vad(sup.vad_from_regions(regions, duration, HOP), HOP, duration)


def _no_overlap(plans):
    return all(a.end <= b.start + 1e-9 for a, b in zip(plans, plans[1:]))


def test_snap_start_inside_speech_moves_back_to_onset():
    v = _vad([(2.0, 5.0)], 10)
    t, in_speech = v.snap_start(2.3, 0.0, 5.0)
    assert abs(t - 2.0) < 0.03 and not in_speech


def test_snap_start_in_silence_moves_forward_to_onset():
    v = _vad([(2.0, 5.0)], 10)
    t, _ = v.snap_start(1.5, 0.0, 5.0)
    assert abs(t - 2.0) < 0.03


def test_snap_end_inside_speech_extends_to_offset_and_in_silence_retracts():
    v = _vad([(2.0, 5.0)], 10)
    t, in_speech = v.snap_end(4.7, 2.0, 10.0)
    assert abs(t - 5.0) < 0.03 and not in_speech
    t, _ = v.snap_end(5.8, 2.0, 10.0)
    assert abs(t - 5.0) < 0.03


def test_snap_falls_back_to_least_speechy_point_in_continuous_speech():
    p = sup.vad_from_regions([(0.0, 20.0)], 20, HOP)
    p[int(9.8 / HOP)] = 0.3  # a brief dip, not a pause
    v = sg.Vad(p, HOP, 20)
    t, in_speech = v.snap_end(10.0, 5.0, 20.0)
    assert abs(t - 9.8) < 0.05 and not in_speech


def test_plan_from_cues_merges_short_cues_and_never_cuts_speech():
    regions = [(1.0, 3.2), (3.5, 6.0), (7.5, 9.0), (9.6, 12.0)]
    v = _vad(regions, 15)
    cues = [sg.CueIn("c1", 1.2, 2.0, "大家好"), sg.CueIn("c2", 2.0, 3.0, "今天天氣"),
            sg.CueIn("c3", 3.5, 6.0, "很好。"), sg.CueIn("c4", 7.3, 9.2, "我們來聊"),
            sg.CueIn("c5", 9.6, 12.1, "台灣的事情。")]
    plans = sg.plan_from_cues(cues, v, sg.Config(min_sec=1.0, max_sec=20.0, pad=0.1))
    assert _no_overlap(plans)
    # c1+c2+c3 (sentence ends at c3), then c4 + c5 (pause 0.6 between is ≥ PAUSE → separate unless short)
    assert plans[0].cue_ids[:3] == ["c1", "c2", "c3"] and plans[0].text == "大家好今天天氣很好。"
    assert abs(plans[0].start - 0.9) < 0.05  # snapped to onset 1.0 minus 0.1 padding
    assert abs(plans[0].end - 6.1) < 0.05
    for p in plans:  # no boundary inside speech
        for a, b in regions:
            assert not (a + 0.05 < p.start < b - 0.05) and not (a + 0.05 < p.end < b - 0.05)


def test_long_cue_is_split_at_pause_with_text_split():
    regions = [(0.5, 12.0), (12.6, 26.0)]
    v = _vad(regions, 30)
    cues = [sg.CueIn("c1", 0.5, 26.0, "第一句話說得很長很長，第二句話也說得很長很長。")]
    plans = sg.plan_from_cues(cues, v, sg.Config(min_sec=1.0, max_sec=20.0, pad=0.1))
    assert len(plans) == 2
    assert 12.0 <= plans[0].end <= 12.4 and 12.2 <= plans[1].start <= 12.6
    assert "text_split" in plans[0].flags
    assert plans[0].text.endswith("，") and plans[1].text.startswith("第二句")


def test_rolling_auto_caption_overlaps_are_clamped():
    v = _vad([(0.0, 10.0)], 10)
    cues = [sg.CueIn("a", 0.0, 3.0, "one"), sg.CueIn("b", 2.0, 5.0, "two")]
    clean = sg.clean_cues(cues, 10)
    assert clean[0].end == 2.0
    assert sg.join_text(["one", "two"]) == "one two" and sg.join_text(["你好", "世界"]) == "你好世界"


def test_speaker_change_cue_flag_blocks_merge():
    v = _vad([(0, 10)], 10)
    cues = [sg.CueIn("a", 0.0, 1.5, "你好"), sg.CueIn("b", 1.5, 3.0, "你好啊", ["speaker_change"])]
    plans = sg.group_cues(cues, v, sg.Config())
    assert [p.cue_ids for p in plans] == [["a"], ["b"]]


def test_plan_from_vad_without_captions():
    regions = [(1.0, 4.0), (4.2, 6.0), (9.0, 35.0)]
    v = _vad(regions, 40)
    plans = sg.plan_from_vad(v, sg.Config(min_sec=1.0, max_sec=20.0, pad=0.1))
    assert _no_overlap(plans)
    assert abs(plans[0].start - 0.9) < 0.05 and abs(plans[0].end - 6.1) < 0.05  # 0.2 s gap merged
    assert all(p.end - p.start <= 20.2 + 1e-6 for p in plans)
    assert sum(1 for p in plans if p.start >= 8.5) == 2  # 26 s region split once
    assert all(p.text == "" for p in plans)


def test_plan_from_vad_splits_at_speaker_change():
    v = _vad([(0.0, 3.0), (3.3, 6.0)], 8)
    turns = [("A", 0.0, 3.0, False), ("B", 3.3, 6.0, False)]
    plans = sg.plan_from_vad(v, sg.Config(min_sec=1.0), turns)
    assert len(plans) == 2


def test_speaker_info_states():
    turns = [("A", 0, 10, False), ("B", 10, 12, False), ("A", 20, 22, True), ("B", 20, 22, True), ("A", 22, 30, False)]
    assert sg.speaker_info(1, 9, turns, True) == ("A", "single")
    assert sg.speaker_info(5, 12, turns, True) == ("A", "multiple")
    assert sg.speaker_info(19, 25, turns, True)[1] == "overlap"
    assert sg.speaker_info(1, 9, [], False) == (None, "unknown")
    assert sg.speaker_info(13, 19, turns, True) == (None, "unknown")


def test_quality_flags_from_thresholds():
    thr = {"low_speech_ratio": 0.5, "low_snr_db": 10, "clipping_ratio": 0.001}
    cfg = sg.Config(min_sec=1.0, max_sec=20.0, pad=0.1)
    f = sg.quality_flags({"speech_ratio": 0.3, "snr": 5.0, "clipping_ratio": 0.01}, 0.8, "overlap", cfg, thr)
    assert f == {"low_speech", "low_snr", "clipped", "too_short", "overlap"}
    assert sg.quality_flags({"speech_ratio": 0.9, "snr": 30.0, "clipping_ratio": 0.0}, 5, "single", cfg, thr) == set()


def test_segment_ids_are_deterministic():
    import uuid
    m = uuid.uuid4()
    assert sg.segment_id(m, 1.0, 2.0) == sg.segment_id(m, 1.0000001, 2.0)
    assert sg.segment_id(m, 1.0, 2.0) != sg.segment_id(m, 1.0, 2.1)


def test_split_overlaps_marks_overlap_pieces():
    turns = [("A", 0.0, 10.0), ("B", 8.0, 15.0)]
    pieces = diarize.split_overlaps(turns)
    assert ("A", 0.0, 8.0, False) in pieces and ("A", 8.0, 10.0, True) in pieces
    assert ("B", 8.0, 10.0, True) in pieces and ("B", 10.0, 15.0, False) in pieces
    assert len(pieces) == 4
    assert diarize.split_overlaps([("A", 0, 5), ("A", 5, 7)]) == [("A", 0, 7, False)]


def test_choose_track_prefers_manual_zh_tw():
    class T:
        def __init__(self, i, kind, lang):
            self.id, self.kind, self.language = i, kind, lang
    tracks = [T("1", "auto", "zh-TW"), T("2", "manual", "en"), T("3", "manual", "zh-TW"), T("4", "translated", "zh")]
    assert sg.choose_track(tracks).id == "3"
    assert sg.choose_track([T("1", "auto", "zh"), T("4", "translated", "zh")]).id == "1"
    assert sg.choose_track([T("4", "translated", "zh")]) is None
    assert sg.choose_track(tracks, preferred_id="2").id == "2"


def test_split_text_prefers_punctuation():
    a, b = sg.split_text("今天天氣很好，我們出去走走吧", 0.45)
    assert a == "今天天氣很好，" and b == "我們出去走走吧"
    assert np.isclose(len(sg.split_text("abcdef", 0.5)[0]), 3)


def test_diarization_missing_or_unauthorized_token_blocks(monkeypatch):
    import pytest
    from voti.config import get_config
    from voti.queue import JobError

    diarize._load_pipeline.cache_clear()
    monkeypatch.setattr(get_config(), "hf_token", "")
    with pytest.raises(JobError) as ei:
        diarize._load_pipeline()
    assert ei.value.code == "hf_token_missing" and ei.value.blocked and "HF_TOKEN" in ei.value.message

    # pyannote returns None when the hub answers 401/403 (terms not accepted / bad token)
    import pyannote.audio
    monkeypatch.setattr(get_config(), "hf_token", "hf_dummy")
    monkeypatch.setattr(pyannote.audio.Pipeline, "from_pretrained", classmethod(lambda cls, *a, **k: None))
    diarize._load_pipeline.cache_clear()
    with pytest.raises(JobError) as ei:
        diarize._load_pipeline()
    assert ei.value.code == "hf_unauthorized" and ei.value.blocked and "接受使用條款" in ei.value.message

    def gated(cls, *a, **k):
        raise type("GatedRepoError", (Exception,), {})("403 Client Error")
    monkeypatch.setattr(pyannote.audio.Pipeline, "from_pretrained", classmethod(gated))
    diarize._load_pipeline.cache_clear()
    with pytest.raises(JobError) as ei:
        diarize._load_pipeline()
    assert ei.value.code == "hf_unauthorized" and ei.value.blocked
    diarize._load_pipeline.cache_clear()
