from twcorpus.subtitles import group_cues, parse_text


def test_srt_chinese_english_tags_overlap_and_rolling():
    cues = parse_text("""1
00:00:00,100 --> 00:00:02,000
<b>你好</b> Hello 42

2
00:00:01,900 --> 00:00:03,000
你好 Hello 42 今天

3
00:00:03,100 --> 00:00:04,000
<i> </i>

4
00:00:04,000 --> 00:00:04,000
壞時間
""", 4.0)
    assert cues[0].cleaned_text == "你好 Hello 42"
    assert "overlap" in cues[1].flags
    assert "possible_rolling_caption" in cues[1].flags
    assert "empty_text" in cues[2].flags
    assert "non_positive_duration" in cues[3].flags


def test_vtt_invalid_negative_out_of_bounds():
    cues = parse_text("""WEBVTT

abc
00:00:00.200 --> 00:00:01.000 align:start
嘿

bad
00:00:ab --> 00:00:04.000
錯誤

-00:00:01.000 --> 00:00:02.000
負時間

00:00:03.000 --> 00:00:06.000
越界
""", 5)
    assert cues[0].original_id == "abc"
    assert "invalid_timestamp" in cues[1].flags
    assert "negative_start" in cues[2].flags
    assert "beyond_audio" in cues[3].flags


def test_segmentation_merges_nearby_never_splits_long_cue():
    cues = parse_text("""1
00:00:00,100 --> 00:00:02,000
甲

2
00:00:02,100 --> 00:00:03,000
乙

3
00:00:03,500 --> 00:00:19,900
長句不可切兩份
""", 20)
    groups = group_cues(cues, 20, padding=0.2)
    assert len(groups) == 2
    assert groups[0]["text"] == "甲 乙"
    assert groups[0]["start_sec"] == 0
    assert groups[0]["end_sec"] == 3.2
    assert groups[1]["text"] == "長句不可切兩份"
    assert groups[1]["end_sec"] == 20
