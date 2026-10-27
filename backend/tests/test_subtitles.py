"""voti.subtitles: VTT/SRT/json3 parsing, cleaning, rolling-caption cleanup, flags."""
from __future__ import annotations

import json

import pytest

from voti.subtitles import clean_text, detect_format, parse_subtitle, parse_timestamp, summary

# Trimmed from a real YouTube ASR WebVTT. "<SP>" marks the whitespace-only lines YouTube puts inside cues
# (written as a marker so editors do not strip them); 10 ms cues are transitions between rolled lines.
ASR_VTT_STRIPPED = """WEBVTT
Kind: captions
Language: en

00:00:10.000 --> 00:00:12.629 align:start position:0%

Good<00:00:10.480><c> morning</c><00:00:10.880><c> everyone.</c><00:00:11.519><c> I</c><00:00:11.679><c> woke</c>

00:00:12.629 --> 00:00:12.639 align:start position:0%
Good morning everyone. I woke
<SP>


00:00:12.639 --> 00:00:14.390 align:start position:0%
Good morning everyone. I woke
up<00:00:12.800><c> at</c><00:00:13.040><c> six</c>

00:00:14.390 --> 00:00:14.400 align:start position:0%
up at six
<SP>


00:00:14.400 --> 00:00:16.390 align:start position:0%
up at six
&gt;&gt; hatch<00:00:14.799><c> alarm,</c><00:00:15.360><c> which</c>

00:00:16.390 --> 00:00:16.400 align:start position:0%
hatch alarm, which
<SP>


00:00:16.400 --> 00:00:18.000 align:start position:0%
hatch alarm, which
[Music]
"""
ASR_VTT = ASR_VTT_STRIPPED.replace("<SP>", " ")
ASR_VTT_STRIPPED = ASR_VTT_STRIPPED.replace("<SP>", "")

ASR_JSON3 = {
    "wireMagic": "pb3", "pens": [{}],
    "events": [
        {"tStartMs": 0, "dDurationMs": 603240, "id": 1, "wpWinPosId": 1, "wsWinStyleId": 1},
        {"tStartMs": 10000, "dDurationMs": 4400, "wWinId": 1,
         "segs": [{"utf8": "Good", "acAsrConf": 0}, {"utf8": " morning", "tOffsetMs": 480}]},
        {"tStartMs": 12629, "dDurationMs": 1771, "wWinId": 1, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 12639, "dDurationMs": 3761, "wWinId": 1,
         "segs": [{"utf8": "a.m."}, {"utf8": " this", "tOffsetMs": 161}]},
        {"tStartMs": 16400, "dDurationMs": 2000, "wWinId": 1, "segs": [{"utf8": "[Music]"}]},
    ],
}

MANUAL_JSON3 = {
    "wireMagic": "pb3",
    "events": [
        {"tStartMs": 3140, "dDurationMs": 1880, "segs": [{"utf8": "大家好 我是烏肉"}]},
        {"tStartMs": 5020, "dDurationMs": 1420, "segs": [{"utf8": "他是小狗阿光"}]},
        {"tStartMs": 7080, "dDurationMs": 2780, "segs": [{"utf8": "你有沒有看過\n呆呆囡仔古的卡通"}]},
    ],
}

NAN_SRT = """1
00:00:03,440 --> 00:00:04,190
逐家好

2
00:00:04,190 --> 00:00:05,120
我是烏肉

3
00:00:07,340 --> 00:00:09,710
你敢有看過呆呆囡仔古的bàng-gah
"""


def test_detect_format():
    assert detect_format(ASR_VTT) == "vtt"
    assert detect_format(json.dumps(MANUAL_JSON3)) == "json3"
    assert detect_format(NAN_SRT) == "srt"
    assert detect_format("﻿WEBVTT\n\n") == "vtt"


@pytest.mark.parametrize("value,sec", [("00:00:03,440", 3.44), ("00:01.5", 1.5), ("1:02:03.004", 3723.004),
                                       ("12:34", 754.0)])
def test_parse_timestamp(value, sec):
    assert parse_timestamp(value) == pytest.approx(sec)


def test_parse_timestamp_invalid():
    with pytest.raises(ValueError):
        parse_timestamp("00:61:00.000")


def test_asr_vtt_rolling_collapsed():
    cues = parse_subtitle(ASR_VTT, "vtt", auto=True)
    texts = [c.text for c in cues]
    assert texts == ["Good morning everyone. I woke", "up at six", "hatch alarm, which", ""]
    assert cues[0].start_sec == 10.0 and cues[0].end_sec == pytest.approx(12.629)
    assert all(c.end_sec <= n.start_sec for c, n in zip(cues, cues[1:]))  # no overlaps after de-rolling
    assert "speaker_change" in cues[2].flags  # ">>" marker
    assert {"empty", "music", "non_speech"} <= set(cues[3].flags)
    assert "<c>" in cues[0].raw_text  # raw keeps the original markup
    assert [c.idx for c in cues] == list(range(len(cues)))


def test_rolling_vtt_with_whitespace_lines_stripped():
    assert [c.text for c in parse_subtitle(ASR_VTT_STRIPPED, "vtt", auto=True)] == \
        [c.text for c in parse_subtitle(ASR_VTT, "vtt", auto=True)]


def test_rolling_detected_without_auto_flag():
    assert [c.text for c in parse_subtitle(ASR_VTT)][:2] == ["Good morning everyone. I woke", "up at six"]


def test_asr_json3():
    cues = parse_subtitle(json.dumps(ASR_JSON3), "json3", auto=True)
    assert [c.text for c in cues] == ["Good morning", "a.m. this", ""]
    assert cues[0].end_sec == pytest.approx(12.639) and "end_trimmed" in cues[0].flags
    assert "overlap" not in cues[1].flags


def test_manual_json3_cjk_line_join():
    cues = parse_subtitle(json.dumps(MANUAL_JSON3).encode(), "json3")
    assert [c.text for c in cues] == ["大家好 我是烏肉", "他是小狗阿光", "你有沒有看過呆呆囡仔古的卡通"]
    assert cues[2].raw_text == "你有沒有看過\n呆呆囡仔古的卡通"
    assert all(c.flags == [] for c in cues)


def test_srt_taiwanese_kept_verbatim():
    cues = parse_subtitle(NAN_SRT.encode("utf-8"), "srt", duration_sec=60)
    assert [c.text for c in cues] == ["逐家好", "我是烏肉", "你敢有看過呆呆囡仔古的bàng-gah"]
    assert cues[1].start_sec == pytest.approx(4.19)


def test_cleaning_rules():
    assert clean_text("<i>Hello</i> <b>world</b>") == "Hello world"
    assert clean_text("&lt;i&gt;escaped&lt;/i&gt; &amp; more") == "escaped & more"
    assert clean_text("♪ 啦啦啦 ♪") == "啦啦啦"
    assert clean_text("[音樂]") == ""
    assert clean_text("（掌聲）謝謝大家") == "謝謝大家"
    assert clean_text("- 你好\n- 我很好") == "你好我很好"
    assert clean_text("- Hi\n- Hello") == "Hi Hello"
    assert clean_text("  多個　　空白\t here  ") == "多個 空白 here"
    assert clean_text("zero​width﻿") == "zerowidth"
    assert clean_text("first line\nsecond line") == "first line second line"
    assert clean_text("中文\nEnglish") == "中文 English"
    assert clean_text("3-2-1 倒數") == "3-2-1 倒數"  # a dash inside text is content


def test_flags_overlap_range_empty_and_merge():
    srt = """1
00:00:01,000 --> 00:00:03,000
- 你好
- 我很好

2
00:00:02,500 --> 00:00:04,000
第二句

3
00:00:04,000 --> 00:00:05,000
第二句

4
00:00:06,000 --> 00:00:05,500
倒過來

5
00:00:07,000 --> 00:00:08,000
♪♪

6
00:00:58,000 --> 00:01:10,000
超出長度
"""
    cues = parse_subtitle(srt, "srt", duration_sec=60)
    by_text = {c.text: c for c in cues}
    assert "speaker_change" in by_text["你好我很好"].flags
    second = by_text["第二句"]
    assert "overlap" in second.flags and "merged_duplicate" in second.flags and second.end_sec == 5.0
    assert "out_of_range" in by_text["倒過來"].flags
    assert {"empty", "music", "non_speech"} <= set(by_text[""].flags)
    assert "out_of_range" in by_text["超出長度"].flags
    s = summary(cues)
    assert s["cues"] == len(cues) and s["usable"] == 2


def test_vtt_notes_styles_ids_and_settings():
    vtt = """WEBVTT - title

NOTE this is a comment
spanning lines

STYLE
::cue { color: red }

cue-1
00:01.000 --> 00:02.000 line:0 position:50%
<v Roger>Hello</v> <c.yellow>there</c>

00:00:02.000 --> 00:00:03.000
second
"""
    cues = parse_subtitle(vtt)
    assert [(c.start_sec, c.end_sec, c.text) for c in cues] == [(1.0, 2.0, "Hello there"), (2.0, 3.0, "second")]


def test_decoding_bom_crlf_and_cp950():
    data = ("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\n台語\r\n\r\n").encode("utf-8")
    assert [c.text for c in parse_subtitle(data)] == ["台語"]
    big5 = "1\n00:00:01,000 --> 00:00:02,000\n台灣\n".encode("cp950")
    assert [c.text for c in parse_subtitle(big5, "srt")] == ["台灣"]


def test_malformed_blocks_skipped():
    srt = "1\nnot a timing\ntext\n\n2\n00:00:01,000 --> 00:00:02,000\nok\n"
    assert [c.text for c in parse_subtitle(srt, "srt")] == ["ok"]
    with pytest.raises(ValueError):
        parse_subtitle("x", "ttml")
