"""Fetch-queue handlers against the test database + bucket, with yt-dlp calls monkeypatched (no network).

Run isolated from other suites:
  VOTI_TEST_DB=voti_test_yt VOTI_TEST_BUCKET=voti-test-yt VOTI_TEST_REDIS_DB=7 pytest tests/test_youtube_handlers.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from voti.sources import youtube as yt

UC = "UChaNiPEqVcO-iE1NIg3wuHQ"


@pytest.fixture
def h(db, monkeypatch):  # noqa: ANN001
    """handlers module with zero rate-limit gaps."""
    from voti.models import Setting
    from voti.sources import handlers

    db.add(Setting(key="youtube", value={"sleep_between_requests_sec": 0, "sleep_between_downloads_sec": 0}))
    db.commit()
    return handlers


def run(db, kind, **kw):  # noqa: ANN001
    """Enqueue + run one job through the real queue. → (Job, follow-up Jobs)"""
    from voti.models import Job
    from voti.queue import enqueue, run_one

    job = enqueue(db, kind, **kw)
    db.commit()
    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    return job, db.scalars(select(Job).where(Job.parent_id == job.id)).all()


def run_existing(db, job):  # noqa: ANN001
    from voti.models import Job
    from voti.queue import run_one

    run_one(str(job.id), "test")
    db.expire_all()
    job = db.get(Job, job.id)
    return job, db.scalars(select(Job).where(Job.parent_id == job.id)).all()


def _entry(vid, kind="video", **kw):
    return {"platform_media_id": vid, "kind": kind, "url": yt.video_url(vid), "title": f"t {vid}",
            "description": "", "duration_sec": 60.0, "view_count": 5, "thumbnail_url": "thumb",
            "published_at": datetime(2024, 1, 1, tzinfo=timezone.utc), "published_approximate": True,
            "channel_id": None, "live_status": None, "availability": None, **kw}


CHANNEL_META = {"platform_channel_id": UC, "handle": "@DaiDaiFriends", "title": "呆呆伙伴", "description": "d",
                "url": yt.channel_url(UC), "thumbnail_url": "avatar", "banner_url": "banner",
                "subscriber_count": 7680, "tags": ["台語"], "metadata": {"uploader": "x"}}


def _info(vid, **kw):
    info = {"id": vid, "title": f"title {vid}", "description": "desc", "duration": 61, "timestamp": 1581912003,
            "view_count": 10, "like_count": 1, "tags": ["台語"], "categories": ["Education"],
            "license": "Creative Commons Attribution license (reuse allowed)", "availability": "public",
            "age_limit": 0, "was_live": False, "live_status": "not_live", "media_type": "video",
            "channel_id": UC, "channel": "呆呆伙伴", "uploader_id": "@DaiDaiFriends",
            "channel_url": yt.channel_url(UC), "extractor": "youtube",
            "formats": [{"format_id": "251", "ext": "webm", "acodec": "opus", "vcodec": "none", "asr": 48000}],
            "subtitles": {"zh-TW": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=zh-TW"}],
                          "nan-TW": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?lang=nan-TW"}]},
            "automatic_captions": {}}
    info.update(kw)
    return info


# ───────────────────────────── resolve / list ─────────────────────────────

def test_resolve_video_creates_media_and_chains_detail(db, h):  # noqa: ANN001
    from voti.models import Media

    job, follow = run(db, "source.resolve", payload={"url": "https://youtube.com/shorts/SY8ItA0IZNQ"})
    assert job.status == "completed", job.error_message
    m = db.scalar(select(Media).where(Media.platform_media_id == "SY8ItA0IZNQ"))
    assert m.kind == "short" and m.status == "listed"
    assert [(f.kind, f.media_id) for f in follow] == [("media.detail", m.id)]


def test_resolve_invalid_url(db, h):  # noqa: ANN001
    job, _ = run(db, "source.resolve", payload={"url": "https://vimeo.com/1"})
    assert job.status == "failed" and job.error_code == "invalid_url"


def test_resolve_channel_then_list(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Channel, Job, Media

    monkeypatch.setattr(yt, "resolve_channel",
                        lambda target, fo: {"channel": CHANNEL_META, "tabs": ["videos", "shorts", "live"],
                                            "warnings": []})
    job, follow = run(db, "source.resolve", payload={"url": "https://www.youtube.com/@DaiDaiFriends/videos"})
    assert job.status == "completed", job.error_message
    ch = db.scalar(select(Channel))
    assert (ch.platform_channel_id, ch.handle, ch.subscriber_count, ch.banner_url) == (UC, "@DaiDaiFriends", 7680,
                                                                                     "banner")
    assert [(f.kind, f.channel_id, f.payload["tabs"]) for f in follow] == [("channel.list", ch.id,
                                                                            ["videos", "shorts"])]

    calls = {}

    def fake_list(channel_id, fo, tabs, limit_per_tab=None, progress=None):
        calls["tabs"] = tabs
        return {"channel": CHANNEL_META, "tabs": {"videos": 2, "shorts": 1},
                "entries": [_entry("aaaaaaaaaaa"), _entry("bbbbbbbbbbb"), _entry("ccccccccccc", "short")],
                "warnings": []}
    monkeypatch.setattr(yt, "list_channel", fake_list)
    db.execute(Job.__table__.delete().where(Job.id == follow[0].id))
    db.commit()
    job, follow = run(db, "channel.list", channel_id=ch.id, payload={"tabs": ["videos", "shorts", "streams"]})
    assert job.status == "completed", job.error_message
    assert calls["tabs"] == ("videos", "shorts")  # streams are never listed
    assert job.result["new"] == 3 and job.result["detail_queued"] is True
    db.refresh(ch)
    assert ch.list_status == "listed" and ch.last_listed_at is not None and ch.video_count == 3
    kinds = {m.platform_media_id: m.kind for m in db.scalars(select(Media))}
    assert kinds == {"aaaaaaaaaaa": "video", "bbbbbbbbbbb": "video", "ccccccccccc": "short"}
    # one chained sweep job for the channel, not one per video
    sweeps = db.scalars(select(Job).where(Job.kind == "media.detail")).all()
    assert len(sweeps) == 1 and sweeps[0].media_id is None and sweeps[0].channel_id == ch.id

    # re-listing does not duplicate rows nor queue a second sweep while one is active
    job, _ = run(db, "channel.list", channel_id=ch.id)
    assert job.result["new"] == 0 and job.result["detail_queued"] is False
    assert len(db.scalars(select(Media)).all()) == 3


def test_channel_list_failure_marks_channel(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Channel

    ch = Channel(platform_channel_id=UC, title="x")
    db.add(ch)
    db.commit()

    def boom(*a, **k):
        raise yt.classify_error("ERROR: Sign in to confirm you’re not a bot")
    monkeypatch.setattr(yt, "list_channel", boom)
    job, _ = run(db, "channel.list", channel_id=ch.id)
    assert job.status == "blocked" and job.error_code == "bot_check" and "cookies" in job.error_message
    db.refresh(ch)
    assert ch.list_status == "failed"


def test_playlist_list(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Job, Playlist, PlaylistItem

    monkeypatch.setattr(yt, "list_playlist", lambda pid, fo, limit=None: {
        "playlist": {"platform_playlist_id": pid, "title": "PL", "description": "", "url": yt.playlist_url(pid),
                     "channel_platform_id": UC, "channel_title": "呆呆伙伴", "channel_handle": "@DaiDaiFriends",
                     "metadata": {"playlist_count": 2}},
        "entries": [_entry("aaaaaaaaaaa", position=0), _entry("bbbbbbbbbbb", position=1)], "warnings": []})
    job, follow = run(db, "source.resolve", payload={"url": "https://www.youtube.com/playlist?list=PLabcdefghijk"})
    assert follow[0].kind == "playlist.list"
    job, _ = run(db, "playlist.list", payload=follow[0].payload)
    assert job.status == "completed", job.error_message
    p = db.scalar(select(Playlist))
    assert p.title == "PL" and p.list_status == "listed" and p.channel_id is not None
    items = db.scalars(select(PlaylistItem).order_by(PlaylistItem.position)).all()
    assert [i.position for i in items] == [0, 1]
    detail = db.scalar(select(Job).where(Job.kind == "media.detail"))
    assert len(detail.payload["media_ids"]) == 2


# ───────────────────────────── detail ─────────────────────────────

def test_detail_single_and_info_json(db, h, monkeypatch):  # noqa: ANN001
    from voti import storage
    from voti.models import Asset, Channel, Media

    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()
    monkeypatch.setattr(yt, "fetch_detail", lambda vid, fo: (_info(vid), []))
    job, _ = run(db, "media.detail", media_id=m.id)
    assert job.status == "completed", job.error_message
    db.refresh(m)
    assert m.status == "detailed" and m.detailed_at is not None and m.license == "creative_commons"
    assert m.has_manual_captions is True and m.manual_caption_langs == ["nan-TW", "zh-TW"]
    assert m.has_auto_captions is False and m.auto_caption_langs == []
    assert m.published_at.year == 2020 and m.metadata_["published_approximate"] is False
    ch = db.get(Channel, m.channel_id)
    assert ch.platform_channel_id == UC  # channel row created from the video
    a = db.get(Asset, m.info_json_asset_id)
    assert a.kind == "info_json" and a.key == f"media/{m.id}/info.json"
    assert json.loads(storage.get_bytes(a.key))["id"] == "aaaaaaaaaaa"


def test_detail_sweep_chains_and_isolates_failures(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Channel, Media

    ch = Channel(platform_channel_id=UC, title="x")
    db.add(ch)
    db.flush()
    ids = ["v%010d" % i for i in range(5)]
    for vid in ids:
        db.add(Media(platform_media_id=vid, channel_id=ch.id, status="listed"))
    db.commit()

    def fake(vid, fo):
        if vid == ids[1]:
            raise yt.classify_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
        return _info(vid), []
    monkeypatch.setattr(yt, "fetch_detail", fake)
    job, follow = run(db, "media.detail", channel_id=ch.id, payload={"batch_size": 2})
    assert job.status == "completed" and job.result["detailed"] == 1
    assert job.result["failed"] == [{"media_id": job.result["failed"][0]["media_id"], "code": "access_restricted"}]
    assert len(follow) == 1 and follow[0].channel_id == ch.id and follow[0].media_id is None
    failed = db.scalar(select(Media).where(Media.platform_media_id == ids[1]))
    assert failed.status == "blocked" and failed.failure_code == "access_restricted"
    # drain the chain by running the queued follow-ups themselves
    rounds = 0
    while follow:
        job, follow = run_existing(db, follow[0])
        assert job.status == "completed", job.error_message
        rounds += 1
    assert rounds == 2  # 5 media, batch 2 → 3 jobs in total
    assert db.scalars(select(Media).where(Media.status == "listed")).all() == []


def test_detail_bot_check_blocks_media(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Media

    m = Media(platform_media_id="aaaaaaaaaaa", status="listed")
    db.add(m)
    db.commit()

    def boom(vid, fo):
        raise yt.classify_error("ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm you’re not a bot.")
    monkeypatch.setattr(yt, "fetch_detail", boom)
    job, _ = run(db, "media.detail", media_id=m.id)
    assert job.status == "blocked" and job.error_code == "bot_check"
    db.refresh(m)
    assert m.status == "blocked" and m.failure_code == "bot_check"


# ───────────────────────────── download ─────────────────────────────

ZH_JSON3 = {"events": [{"tStartMs": 1000, "dDurationMs": 1500, "segs": [{"utf8": "大家好"}]},
                       {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "[音樂]"}]}]}
NAN_JSON3 = {"events": [{"tStartMs": 1000, "dDurationMs": 1500, "segs": [{"utf8": "逐家好"}]}]}


def _fake_download(captured):
    def fake(vid, workdir, fo, caption_langs=None, include_auto=False, require_captions=True, progress=None,
             should_cancel=None):
        captured.update(caption_langs=caption_langs, include_auto=include_auto, cookiefile=fo.cookiefile,
                        cookie_text=Path(fo.cookiefile).read_text() if fo.cookiefile else None)
        audio = Path(workdir) / "original.webm"
        audio.write_bytes(b"\x1aE\xdf\xa3" + b"0" * 1000)
        subs = []
        for lang, data, primary in (("zh-TW", ZH_JSON3, True), ("nan-TW", NAN_JSON3, False)):
            p = Path(workdir) / f"sub.{lang}.manual.json3"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            subs.append({"key": lang, "language": lang, "kind": "manual", "primary": primary, "path": p,
                         "format": "json3"})
        if progress:
            progress(0.5, "audio")
        return yt.DownloadResult(info=_info(vid), audio_path=audio, audio_format={"format_id": "251", "ext": "webm"},
                                 subtitles=subs, subtitle_errors=[], warnings=[])
    return fake


def test_download_stores_audio_tracks_cues(db, h, monkeypatch):  # noqa: ANN001
    from voti import storage
    from voti.models import Asset, Cue, Media, Track

    m = Media(platform_media_id="aaaaaaaaaaa", status="detailed")
    db.add(m)
    db.commit()
    captured = {}
    monkeypatch.setattr(yt, "download", _fake_download(captured))
    job, follow = run(db, "media.download", media_id=m.id, payload={"caption_langs": ["zh-TW"]})
    assert job.status == "completed", job.error_message
    assert captured["caption_langs"] == ["zh-TW"] and captured["include_auto"] is False
    assert [(f.kind, f.media_id) for f in follow] == [("media.normalize", m.id)]
    db.refresh(m)
    assert m.status == "downloaded" and m.stage is None
    assert m.metadata_["download"]["primary_track"]["language"] == "zh-TW"
    keys = {a.kind + ":" + a.key.split("/", 2)[2] for a in db.scalars(select(Asset).where(Asset.media_id == m.id))}
    assert keys == {"info_json:info.json", "original_audio:original.webm", "subtitle:subtitles/zh-TW.manual.json3",
                    "subtitle:subtitles/nan-TW.manual.json3"}
    assert storage.head(f"media/{m.id}/original.webm")["ContentLength"] == 1004
    tracks = {t.language: t for t in db.scalars(select(Track).where(Track.media_id == m.id))}
    assert set(tracks) == {"zh-TW", "nan-TW"} and tracks["zh-TW"].kind == "manual" and tracks["zh-TW"].format == "json3"
    cues = db.scalars(select(Cue).where(Cue.track_id == tracks["zh-TW"].id).order_by(Cue.idx)).all()
    assert [(c.idx, c.text) for c in cues] == [(0, "大家好"), (1, "")]
    assert "non_speech" in cues[1].flags and cues[1].raw_text == "[音樂]"

    # re-download: same Track rows reused, cues replaced (not duplicated)
    zh_id = tracks["zh-TW"].id
    db.query(Media).filter_by(id=m.id).update({"status": "detailed"})
    db.commit()
    job, _ = run(db, "media.download", media_id=m.id)
    assert job.status == "completed", job.error_message
    assert db.scalar(select(Track).where(Track.media_id == m.id, Track.language == "zh-TW")).id == zh_id
    assert len(db.scalars(select(Cue).where(Cue.track_id == zh_id)).all()) == 2


def test_download_uses_cookies_only_when_enabled(db, h, monkeypatch):  # noqa: ANN001
    from voti import storage
    from voti.models import Media, Setting

    storage.put_bytes(b"# Netscape HTTP Cookie File\n", "system/cookies.txt", "text/plain")
    try:
        m = Media(platform_media_id="aaaaaaaaaaa", status="detailed")
        db.add(m)
        db.commit()
        captured = {}
        monkeypatch.setattr(yt, "download", _fake_download(captured))
        run(db, "media.download", media_id=m.id)
        assert captured["cookiefile"] is None

        s = db.get(Setting, "youtube")
        s.value = {**s.value, "cookies_enabled": True}
        db.commit()
        db.query(Media).filter_by(id=m.id).update({"status": "detailed"})
        db.commit()
        run(db, "media.download", media_id=m.id)
        assert captured["cookie_text"].startswith("# Netscape")
        assert not Path(captured["cookiefile"]).exists()  # temp copy removed after the job
    finally:
        storage.delete("system/cookies.txt")


def test_download_no_usable_captions(db, h, monkeypatch):  # noqa: ANN001
    from voti.models import Media

    m = Media(platform_media_id="aaaaaaaaaaa", status="detailed")
    db.add(m)
    db.commit()

    def fake(*a, **k):
        raise yt.YouTubeError("no_usable_captions", "影片沒有任何字幕軌", retryable=False)
    monkeypatch.setattr(yt, "download", fake)
    job, follow = run(db, "media.download", media_id=m.id)
    assert job.status == "failed" and job.error_code == "no_usable_captions" and follow == []
    db.refresh(m)
    assert m.status == "skipped" and m.failure_code == "no_usable_captions"


def test_upsert_channel_replaces_placeholder(db, h):  # noqa: ANN001
    from voti.models import Channel

    real = Channel(platform_channel_id=UC, title="real")
    placeholder = Channel(platform_channel_id="@DaiDaiFriends", handle="@DaiDaiFriends")
    db.add_all([real, placeholder])
    db.commit()
    row = h.upsert_channel(db, CHANNEL_META, db.get(Channel, placeholder.id))
    db.commit()
    assert row.id == real.id and db.get(Channel, placeholder.id) is None
    assert uuid.UUID(str(row.id))
