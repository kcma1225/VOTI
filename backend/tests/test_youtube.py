"""Pure tests for voti.sources.youtube (no network, no database)."""
from __future__ import annotations

import pytest

from voti.sources import youtube as yt

VID = "SY8ItA0IZNQ"
UC = "UChaNiPEqVcO-iE1NIg3wuHQ"
PL = "PLgvhchAPGWYPCchYKNNGZWJrfrw6CVY2K"


# ───────────────────────────── classify_url ─────────────────────────────

@pytest.mark.parametrize("url,expected", [
    (f"https://www.youtube.com/watch?v={VID}", {"type": "video", "video_id": VID}),
    (f"http://youtube.com/watch?v={VID}&t=30s", {"type": "video", "video_id": VID}),
    (f"https://m.youtube.com/watch?v={VID}", {"type": "video", "video_id": VID}),
    (f"youtube.com/watch?v={VID}", {"type": "video", "video_id": VID}),
    (f"https://youtu.be/{VID}", {"type": "video", "video_id": VID}),
    (f"https://youtu.be/{VID}?si=abc&t=1", {"type": "video", "video_id": VID}),
    (f"https://www.youtube.com/shorts/{VID}", {"type": "video", "video_id": VID, "kind_hint": "short"}),
    (f"https://www.youtube.com/live/{VID}?feature=share", {"type": "video", "video_id": VID, "kind_hint": "live"}),
    (f"https://www.youtube.com/embed/{VID}", {"type": "video", "video_id": VID}),
    (f"https://www.youtube.com/watch?v={VID}&list={PL}&index=3",
     {"type": "video", "video_id": VID, "playlist_id": PL}),
    (f"https://youtu.be/{VID}?list={PL}", {"type": "video", "video_id": VID, "playlist_id": PL}),
    (f"https://www.youtube.com/playlist?list={PL}", {"type": "playlist", "playlist_id": PL}),
    (f"https://www.youtube.com/watch?list={PL}", {"type": "playlist", "playlist_id": PL}),
    ("https://www.youtube.com/@DaiDaiFriends", {"type": "channel", "handle": "@DaiDaiFriends"}),
    ("https://www.youtube.com/@DaiDaiFriends/", {"type": "channel", "handle": "@DaiDaiFriends"}),
    ("https://www.youtube.com/@DaiDaiFriends/videos", {"type": "channel", "handle": "@DaiDaiFriends", "tab": "videos"}),
    ("https://www.youtube.com/@DaiDaiFriends/shorts", {"type": "channel", "handle": "@DaiDaiFriends", "tab": "shorts"}),
    ("https://www.youtube.com/@x.y-z/streams", {"type": "channel", "handle": "@x.y-z", "tab": "streams"}),
    ("https://www.youtube.com/@ptstaigitai/playlists",
     {"type": "channel", "handle": "@ptstaigitai", "tab": "playlists"}),
    ("https://www.youtube.com/@ptstaigitai/featured", {"type": "channel", "handle": "@ptstaigitai", "tab": "featured"}),
    ("https://www.youtube.com/@%E4%BD%AE%E5%9B%A1%E4%BB%94%E5%AD%B8%E5%8F%B0%E8%AA%9E",
     {"type": "channel", "handle": "@佮囡仔學台語"}),
    (f"https://www.youtube.com/channel/{UC}", {"type": "channel", "channel_id": UC}),
    (f"https://www.youtube.com/channel/{UC}/videos", {"type": "channel", "channel_id": UC, "tab": "videos"}),
    ("https://www.youtube.com/c/SomeName", {"type": "channel", "custom_name": "SomeName"}),
    ("https://www.youtube.com/user/someuser/videos", {"type": "channel", "custom_name": "someuser", "tab": "videos"}),
    ("@DaiDaiFriends", {"type": "channel", "handle": "@DaiDaiFriends"}),
    (UC, {"type": "channel", "channel_id": UC}),
])
def test_classify_url(url, expected):
    got = yt.classify_url(url)
    for k, v in expected.items():
        assert got.get(k) == v, (k, got)
    assert got["canonical_url"].startswith("https://www.youtube.com/")


def test_classify_canonical_urls():
    assert yt.classify_url(f"https://youtu.be/{VID}")["canonical_url"] == f"https://www.youtube.com/watch?v={VID}"
    assert yt.classify_url(f"https://www.youtube.com/channel/{UC}/shorts")["canonical_url"] == \
        f"https://www.youtube.com/channel/{UC}"
    assert yt.classify_url("https://www.youtube.com/@DaiDaiFriends/videos")["canonical_url"] == \
        "https://www.youtube.com/@DaiDaiFriends"
    assert yt.classify_url(f"https://www.youtube.com/playlist?list={PL}&si=x")["canonical_url"] == \
        f"https://www.youtube.com/playlist?list={PL}"


@pytest.mark.parametrize("url", [
    "", "   ", "https://vimeo.com/123", "https://www.youtube.com/", "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/feed/subscriptions", "https://www.youtube.com/@x/community",
    "https://www.youtube.com/channel/notachannel", "https://youtu.be/", "ftp://youtube.com/watch?v=" + VID,
    "https://evil.example/www.youtube.com/watch?v=" + VID, "https://www.youtube.com/playlist",
    "https://www.youtube.com/@x/videos/extra", "https://www.youtube.com/results?search_query=x",
])
def test_classify_url_rejects(url):
    with pytest.raises(ValueError):
        yt.classify_url(url)


# ───────────────────────────── captions ─────────────────────────────

def _fmts(lang, *, kind=None, tlang=None, hls=False, name=None):
    if hls:
        return [{"ext": "vtt", "url": "https://manifest.googlevideo.com/api/manifest/hls_timedtext_playlist/x"}]
    q = f"v={VID}&lang={lang}" + (f"&kind={kind}" if kind else "") + (f"&tlang={tlang}" if tlang else "")
    return [{"ext": e, "name": name, "url": f"https://www.youtube.com/api/timedtext?{q}&fmt={e}"}
            for e in ("json3", "srv1", "vtt", "srt")]


def test_caption_tracks_manual_and_hls_manifests_are_not_auto():
    info = {"subtitles": {"zh-TW": _fmts("zh-TW", name="Chinese (Taiwan)"), "nan-TW": _fmts("nan-TW"),
                          "live_chat": [{"ext": "json"}]},
            # some clients list an HLS timedtext manifest for every track: not ASR
            "automatic_captions": {"zh-TW": _fmts("zh-TW", hls=True), "nan-TW": _fmts("nan-TW", hls=True)}}
    caps = yt.caption_tracks(info)
    assert caps["manual"] == {"zh-TW": "Chinese (Taiwan)", "nan-TW": "nan-TW"}
    assert caps["auto"] == {}


def test_caption_tracks_asr_original_only():
    ac = {"en-orig": _fmts("en", kind="asr", name="English (Original)"), "en": _fmts("en", kind="asr"),
          "ab": _fmts("en", kind="asr", tlang="ab"), "zh-Hant": _fmts("en", kind="asr", tlang="zh-Hant"),
          "zh-TW-en": _fmts("en", tlang="zh-TW")}
    caps = yt.caption_tracks({"subtitles": {}, "automatic_captions": ac})
    assert caps["auto"] == {"en": {"key": "en-orig", "name": "English (Original)"}}


def test_caption_tracks_untranslatable_asr_without_orig():
    ac = {"yue": _fmts("yue", kind="asr"), "en": _fmts("yue", kind="asr", tlang="en")}
    assert yt.caption_tracks({"automatic_captions": ac})["auto"] == {"yue": {"key": "yue", "name": "yue"}}


# ───────────────────────────── choose_tracks ─────────────────────────────

def _caps(manual=(), auto=()):
    return {"manual": {k: k for k in manual}, "auto": {k: {"key": f"{k}-orig", "name": k} for k in auto}}


def test_choose_default_prefers_traditional_and_keeps_taiwanese():
    chosen = yt.choose_tracks(_caps(["en", "ja", "nan-TW", "zh-TW"]))
    assert [(c["key"], c["kind"], c["primary"]) for c in chosen] == [("zh-TW", "manual", True),
                                                                     ("nan-TW", "manual", False)]


@pytest.mark.parametrize("manual,primary", [
    (["zh-Hant", "zh-Hans"], "zh-Hant"), (["zh", "en"], "zh"), (["zh-CN", "en"], "zh-CN"), (["en", "ja"], "en"),
    (["zh-TW-abc123"], "zh-TW-abc123"), (["zh-HK", "zh-CN"], "zh-HK"),
])
def test_choose_default_fallbacks(manual, primary):
    assert yt.choose_tracks(_caps(manual))[0]["key"] == primary


def test_choose_auto_only_when_requested():
    caps = _caps([], ["zh"])
    assert yt.choose_tracks(caps) == []
    chosen = yt.choose_tracks(caps, include_auto=True)
    assert chosen == [{"key": "zh-orig", "language": "zh", "kind": "auto", "primary": True, "rank": 3}]


def test_choose_manual_wins_primary_over_auto():
    chosen = yt.choose_tracks(_caps(["zh-TW"], ["zh"]), include_auto=True)
    assert [(c["key"], c["kind"], c["primary"]) for c in chosen] == [("zh-TW", "manual", True),
                                                                     ("zh-orig", "auto", False)]


def test_choose_explicit_chinese_falls_back_to_other_chinese_variant():
    caps = _caps(["en", "zh-Hans"], [])
    assert [c["key"] for c in yt.choose_tracks(caps, ["zh-TW"])] == ["zh-Hans"]
    assert [c["key"] for c in yt.choose_tracks(caps, ["en"])] == ["en"]
    assert yt.choose_tracks(_caps(["en"], []), ["zh-TW"]) == []


def test_choose_explicit_langs():
    caps = _caps(["en", "nan-TW", "zh-TW"], ["en"])
    assert [c["key"] for c in yt.choose_tracks(caps, ["nan-TW"])] == ["nan-TW"]
    assert [c["key"] for c in yt.choose_tracks(caps, ["fr"])] == []
    assert [(c["key"], c["kind"]) for c in yt.choose_tracks(caps, ["en"], include_auto=True)] == \
        [("en", "manual"), ("en-orig", "auto")]
    # auto filtered by requested language
    assert [c["key"] for c in yt.choose_tracks(caps, ["zh-TW"], include_auto=True)] == ["zh-TW"]


# ───────────────────────────── summarize_detail ─────────────────────────────

def _info(**kw):
    base = {
        "id": VID, "title": "《台灣味ê料理救援隊》台語教室 EP7", "description": "desc", "duration": 163,
        "timestamp": 1581912003, "upload_date": "20200217", "release_timestamp": None,
        "view_count": 3410, "like_count": 20, "comment_count": None, "tags": ["台語"], "categories": ["Education"],
        "chapters": [{"start_time": 0.0, "end_time": 10.0, "title": "intro"}], "language": None,
        "license": None, "availability": "public", "age_limit": 0, "was_live": False, "live_status": "not_live",
        "media_type": "video", "channel_id": UC, "channel": "呆呆伙伴", "uploader_id": "@DaiDaiFriends",
        "thumbnail": "https://i.ytimg.com/vi/x/maxresdefault.jpg",
        "formats": [{"format_id": "251", "ext": "webm", "acodec": "opus", "vcodec": "none", "asr": 48000,
                     "abr": 137.1, "language": "zh-TW"},
                    {"format_id": "18", "ext": "mp4", "acodec": "mp4a.40.2", "vcodec": "avc1"},
                    {"format_id": "sb0", "ext": "mhtml", "acodec": "none", "vcodec": "none"}],
        "subtitles": {"zh-TW": _fmts("zh-TW")}, "automatic_captions": {},
    }
    base.update(kw)
    return base


def test_summarize_detail_basic():
    d = yt.summarize_detail(_info())
    assert d["platform_media_id"] == VID and d["url"].endswith(VID)
    assert d["kind"] == "video" and d["license"] == "youtube" and d["duration_sec"] == 163.0
    assert d["published_at"].year == 2020 and d["published_at"].tzinfo is not None
    assert d["has_manual_captions"] is True and d["has_auto_captions"] is False
    assert d["manual_caption_langs"] == ["zh-TW"] and d["auto_caption_langs"] == []
    assert d["chapters"] == [{"start_sec": 0.0, "end_sec": 10.0, "title": "intro"}]
    assert d["language"] == "zh-TW"  # from the audio format when the video declares none
    assert d["was_live"] is False and d["age_limit"] == 0
    md = d["metadata"]
    assert md["channel_platform_id"] == UC and md["uploader_id"] == "@DaiDaiFriends"
    assert [f["format_id"] for f in md["audio_formats"]] == ["251"]
    assert md["published_approximate"] is False


def test_summarize_detail_license_and_kinds():
    cc = yt.summarize_detail(_info(license="Creative Commons Attribution license (reuse allowed)"))
    assert cc["license"] == "creative_commons"
    assert yt.summarize_detail(_info(license="Standard YouTube License"))["license"] == "youtube"
    assert yt.summarize_detail(_info(media_type="short"))["kind"] == "short"
    live = yt.summarize_detail(_info(media_type="livestream", was_live=True, live_status="was_live"))
    assert live["kind"] == "live" and live["was_live"] is True
    no_ts = yt.summarize_detail(_info(timestamp=None))
    assert no_ts["published_at"].isoformat().startswith("2020-02-17T00:00:00")


def test_map_license():
    assert yt.map_license("Creative Commons Attribution license (reuse allowed)") == "creative_commons"
    assert yt.map_license(None) == "youtube"


# ───────────────────────────── listing helpers ─────────────────────────────

def test_flat_entry_and_channel_meta():
    e = {"id": VID, "title": "t", "duration": 216, "view_count": 4500, "timestamp": 1729987200,
         "url": f"https://www.youtube.com/watch?v={VID}",
         "thumbnails": [{"url": "a", "width": 360, "height": 202}, {"url": "b", "width": 720, "height": 404}]}
    row = yt._flat_entry(e, "video")
    assert row["kind"] == "video" and row["duration_sec"] == 216.0 and row["thumbnail_url"] == "b"
    assert row["published_at"].year == 2024 and row["published_approximate"] is True
    short = yt._flat_entry({"id": VID, "url": f"https://www.youtube.com/shorts/{VID}"}, "video")
    assert short["kind"] == "short" and short["published_at"] is None
    assert short["thumbnail_url"] == f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg"
    assert yt._flat_entry({"id": VID, "live_status": "is_upcoming"}, "video") is None
    assert yt._flat_entry({"id": "UCnotavideo_______________"}, "video") is None

    meta = yt._channel_meta({
        "channel_id": UC, "channel": "呆呆伙伴", "uploader_id": "@DaiDaiFriends", "description": "d",
        "channel_follower_count": 7680, "tags": ["台語"], "channel_url": f"https://www.youtube.com/channel/{UC}",
        "thumbnails": [{"id": "0", "url": "wide", "width": 1060}, {"id": "banner_uncropped", "url": "banner"},
                       {"id": "7", "url": "sq", "width": 900, "height": 900},
                       {"id": "avatar_uncropped", "url": "avatar"}]})
    assert meta["platform_channel_id"] == UC and meta["handle"] == "@DaiDaiFriends"
    assert meta["thumbnail_url"] == "avatar" and meta["banner_url"] == "banner"
    assert meta["subscriber_count"] == 7680 and meta["title"] == "呆呆伙伴"


# ───────────────────────────── errors / options ─────────────────────────────

@pytest.mark.parametrize("message,code,blocked", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies "
     "for the authentication.", "bot_check", True),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
     "age_restricted", True),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video",
     "members_only", True),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "access_restricted",
     True),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "region_restricted",
     True),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", "unavailable", False),
    ("ERROR: [youtube] xxxxxxxxxx0: This video is unavailable", "unavailable", False),
    ("ERROR: [youtube:tab] @nope/videos: Unable to download API page: HTTP Error 404: Not Found", "unavailable",
     False),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", "rate_limited", True),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", "upcoming", False),
    ("ERROR: [youtube] abc: Requested format is not available", "format_unavailable", False),
    ("ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>",
     "network_or_extractor", False),
])
def test_classify_error(message, code, blocked):
    e = yt.classify_error(Exception(message))
    assert (e.code, e.blocked) == (code, blocked)
    assert e.message


def test_base_opts():
    opts = yt.base_opts(yt.FetchOptions(rate_limit_kbps=500, cookiefile="/tmp/c.txt"))
    assert opts["ratelimit"] == 500 * 1024 and opts["cookiefile"] == "/tmp/c.txt"
    assert opts["sleep_interval_requests"] > 0
    assert "cookiesfrombrowser" not in opts
    opts = yt.base_opts(yt.FetchOptions())
    assert "ratelimit" not in opts and "cookiefile" not in opts


# ───────────────────────────── caption preference (Traditional first, Simplified converted) ─────────────────────────────

@pytest.mark.parametrize("lang,rank", [
    ("zh-TW", 0), ("zh-Hant-TW", 0), ("zh-TW-abc", 0), ("zh-Hant", 1), ("zh-Hant-HK", 1), ("zh-HK", 2), ("zh-MO", 2),
    ("zh", 3), ("cmn", 3), ("zh-xyz", 3), ("zh-Hans", 4), ("zh-Hans-CN", 4), ("zh-CN", 4), ("zh-SG", 4),
    ("zh-MY", 4), ("nan-TW", 5), ("hak-TW", 5), ("en", 6), ("ja", 6),
])
def test_caption_rank(lang, rank):
    assert yt.caption_rank(lang) == rank


@pytest.mark.parametrize("manual,primary", [
    (["zh-CN", "zh-TW", "zh-HK"], "zh-TW"),
    (["zh-Hans", "zh-HK"], "zh-HK"),
    (["zh-Hans", "zh-Hant-HK"], "zh-Hant-HK"),
    (["zh-Hans", "zh"], "zh"),                 # script unknown beats tagged Simplified (content decides later)
    (["en", "zh-CN"], "zh-CN"),                # Simplified-only video: still Chinese, converted on ingestion
    (["en", "zh-SG", "ja"], "zh-SG"),
    (["en", "nan-TW"], "nan-TW"),              # no Chinese: Taiwanese before other languages
    (["fr", "en"], "en"),                      # never machine-translated; shortest/alphabetical other track
])
def test_choose_prefers_traditional_then_simplified(manual, primary):
    chosen = yt.choose_tracks(_caps(manual))
    assert chosen[0]["key"] == primary and chosen[0]["primary"] is True


def test_choose_simplified_only_keeps_taiwanese_and_marks_rank():
    chosen = yt.choose_tracks(_caps(["zh-CN", "hak-TW", "en"]))
    assert [(c["key"], c["primary"], c["rank"]) for c in chosen] == [("zh-CN", True, 4), ("hak-TW", False, 5)]


def test_choose_ambiguous_zh_downloads_all_candidates_for_content_detection():
    chosen = yt.choose_tracks(_caps(["zh", "cmn", "zh-Hans", "en"]))
    assert [(c["key"], c["primary"]) for c in chosen] == [("zh", True), ("cmn", False)]


def test_choose_auto_follows_same_preference():
    chosen = yt.choose_tracks(_caps([], ["en", "zh-CN", "zh-TW"]), include_auto=True)
    assert [(c["language"], c["primary"]) for c in chosen] == [("zh-TW", True), ("zh-CN", False), ("en", False)]
    assert yt.choose_tracks(_caps([], ["zh-CN"])) == []  # auto captions stay opt-in


def test_repick_primary_by_script():
    from voti.sources.handlers import repick_primary_by_script
    tracks = [{"language": "zh", "kind": "manual", "primary": True, "script": "simplified", "rank": 3},
              {"language": "cmn", "kind": "manual", "primary": False, "script": "traditional", "rank": 3},
              {"language": "nan-TW", "kind": "manual", "primary": False, "script": "traditional", "rank": 5}]
    repick_primary_by_script(tracks)
    assert [t["primary"] for t in tracks] == [False, True, False]
    # a better-ranked Simplified track is never displaced by a lower tier, and Traditional primaries stay
    tracks = [{"language": "zh-CN", "kind": "manual", "primary": True, "script": "simplified", "rank": 4},
              {"language": "nan-TW", "kind": "manual", "primary": False, "script": "traditional", "rank": 5}]
    repick_primary_by_script(tracks)
    assert tracks[0]["primary"] is True
