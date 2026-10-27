import re
import subprocess
import warnings

import pytest

from twcorpus.config import Settings
from twcorpus.core import list_segments, list_sources, update_source

warnings.filterwarnings("ignore", category=DeprecationWarning)
from starlette.testclient import TestClient  # noqa: E402

from twcorpus.web.app import create_app  # noqa: E402

SOURCE_SUBPAGES = ("", "/manage", "/edit", "/fetch", "/tracks", "/audio", "/review")
PAGES = ("/", "/sources", "/sources?tab=urls", "/sources?tab=candidates", "/segments", "/clusters", "/export")
HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)>'


@pytest.fixture
def client(tmp_path):
    settings = Settings(tmp_path / "data")
    settings.data_dir.mkdir()
    return TestClient(create_app(settings, ["testserver"])), settings, tmp_path


def make_media(tmp_path):
    audio, subtitle = tmp_path / "a.wav", tmp_path / "a.srt"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-ac", "1", "-ar", "16000", "-y", str(audio)], check=True)
    subtitle.write_text(f"1\n00:00:00,500 --> 00:00:02,000\n你好 {HOSTILE}\n\n"
                        "2\n00:00:05,000 --> 00:00:07,000\n第二段\n", encoding="utf-8")
    return audio, subtitle


def test_empty_pages_render_with_theme_and_csp(client):
    app, _, _ = client
    for path in PAGES:
        response = app.get(path)
        assert response.status_code == 200, path
        assert "data-theme-toggle" in response.text
        assert "theme.js" in response.text and "jobs.js" in response.text and "select.js" in response.text
        assert "島嶼之聲" in response.text and "mark.svg?v=" in response.text and "favicon.svg?v=" in response.text
        # Content-hashed URLs: a rebuilt image is never masked by the browser's cached copy.
        assert re.search(r'/static/app\.css\?v=[0-9a-f]{12}"', response.text)
        assert 'id="jobs-panel"' in response.text and "data-jobs-toggle" in response.text and "data-toasts" in response.text
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["referrer-policy"] == "same-origin"
    assert app.get("/api/healthz").json()["status"] == "ok"
    css = app.get("/static/app.css").text
    assert ':root[data-theme="dark"]' in css and "prefers-color-scheme: dark" in css
    # "anywhere" let table cells shrink to one glyph and split words such as "YouTube".
    assert "overflow-wrap: anywhere;\n}" not in css.split("/* ---------- shell")[0]
    jobs_page = app.get("/jobs", follow_redirects=False)
    assert jobs_page.status_code in (302, 303, 307) and jobs_page.headers["location"] == "/?jobs=open"


def test_import_review_cluster_export_flow_escapes_external_text(client):
    app, settings, tmp_path = client
    audio, subtitle = make_media(tmp_path)
    with audio.open("rb") as a, subtitle.open("rb") as s:
        response = app.post("/api/sources/local", files={"audio": ("a.wav", a, "audio/wav"),
                                                     "subtitle": ("a.srt", s, "application/x-subrip")},
                            data={"title": HOSTILE, "language": "zh", "subtitle_language": "zh",
                                  "subtitle_kind": "local_provided", "speaker_count": "single",
                                  "taiwan_basis": "unknown", "license_label": "unknown", "license_evidence": ""},
                            follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/sources?") and "notice=queued" in location and "jobs=open" in location
    # The request only stores the upload; the worker imports, normalizes and slices it in the background.
    assert not list_sources(settings)
    queued = app.get("/api/jobs").json()
    assert queued["active"] == 1 and queued["jobs"][0]["kind"] == "local_import"
    assert queued["jobs"][0]["kind_label"] == "本地匯入" and queued["jobs"][0]["eta_sec"] is None
    from twcorpus.jobs import run_one
    run_one(settings)
    finished = app.get("/api/jobs").json()["jobs"][0]
    assert finished["status"] == "completed", finished
    assert finished["percent"] == 100 and finished["source_id"]
    assert not any((settings.data_dir / "uploads").iterdir()), "temporary uploads must be removed"
    sid = list_sources(settings)[0]["id"]
    segments = list_segments(settings)
    assert len(segments) == 2
    for path in PAGES + tuple(f"/sources/{sid}{sub}" for sub in SOURCE_SUBPAGES) + (f"/sources/{sid}/review?segment={segments[0]['id']}",):
        page = app.get(path)
        assert page.status_code == 200, path
        assert "<script>alert" not in page.text and "<img src=x" not in page.text, path
    assert "&lt;script&gt;" in app.get(f"/sources/{sid}").text and "&lt;script&gt;" in app.get(f"/sources/{sid}/edit").text

    audio_response = app.get(f"/api/media/segments/{segments[0]['id']}", headers={"range": "bytes=0-99"})
    assert audio_response.status_code == 206 and len(audio_response.content) == 100
    full = app.get(f"/api/media/sources/{sid}", headers={"range": "bytes=0-1023"})
    assert full.status_code == 206 and len(full.content) == 1024 and full.content[:3] == b"ID3"
    assert full.headers["content-type"] == "audio/mpeg"
    source_page = app.get(f"/sources/{sid}").text
    assert "data-transcript" in source_page and f'src="/api/media/sources/{sid}"' in source_page
    assert 'data-seek="0.5"' in source_page and "第二段" in source_page and "player.js" in source_page
    # Word alignment is offered for same-language zh/en tracks only.
    assert f'action="/api/sources/{sid}/align"' in app.get(f"/sources/{sid}/audio").text
    # The main page stays focused: transcript and metadata only, no forms (everything else is a sub-page).
    assert "<form method=\"post\"" not in source_page and f'href="/sources/{sid}/manage"' in source_page

    # Review lives inside the source: not in the sidebar, old URL redirects to the owning source.
    review_page = app.get(f"/sources/{sid}/review").text
    assert "data-live" in review_page and "套用</button>" not in review_page.replace("<noscript><button class=\"btn\" type=\"submit\">套用</button></noscript>", "")
    assert 'href="/segments"' in app.get("/").text and 'href="/review"' not in app.get("/").text
    listing = app.get(f"/segments?source={sid}").text
    assert f'href="/sources/{sid}/review?segment={segments[0]["id"]}"' in listing and "data-live" in listing
    moved = app.get(f"/review?segment={segments[0]['id']}", follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"].startswith(f"/sources/{sid}/review?")
    assert app.get("/review", follow_redirects=False).headers["location"] == "/segments"
    live = app.get("/sources").text
    assert "data-live" in live and "data-live-region=\"results\"" in live and "live.js" in live

    target = segments[0]["id"]
    saved = app.post(f"/api/review/{target}", data={
        "corrected_text": "你好，臺灣", "note": "", "start_sec": str(segments[0]["start_sec"]),
        "end_sec": str(segments[0]["end_sec"]), "review_status": "passed", "review_level": "fully_corrected",
        "language": "zh", "speaker_state": "single", "local_speaker_id": "A", "manual_labels": ["noise"],
        "source_id": sid}, follow_redirects=False)
    assert saved.status_code == 303
    updated = list_segments(settings, segment_id=target)[0]
    assert updated["corrected_text"] == "你好，臺灣" and updated["local_speaker_id"] == f"{sid}:A"
    assert '"noise"' in updated["manual_labels_json"]

    bad = app.post(f"/api/review/{target}", data={"start_sec": "0", "end_sec": "1", "review_status": "bogus",
                                             "review_level": "none", "language": "zh", "speaker_state": "unknown"})
    assert bad.status_code == 400 and "審查狀態 的值無效" in bad.text

    clustered = app.post("/api/clusters/run", data={"threshold": "0.35"})
    assert clustered.status_code == 400 and "embedding" in clustered.text
    grouped = app.post(f"/api/clusters/{target}/group", data={"group": "speaker_x", "note": ""}, follow_redirects=False)
    assert grouped.status_code == 303
    assert "speaker_x" in app.get("/clusters?group=speaker_x").text

    exported = app.post("/api/export", data={"status": "passed", "corrected_only": "yes"}, follow_redirects=False)
    assert exported.status_code == 303
    export_id = exported.headers["location"].split("ref=")[1]
    manifest = app.get(f"/api/exports/{export_id}/manifest.jsonl")
    assert manifest.status_code == 200 and "publication_rights_confirmed" in manifest.text
    assert app.get(f"/api/exports/{export_id}/../corpus.sqlite3").status_code == 404
    assert app.get("/api/exports/abc/manifest.jsonl").status_code == 404


def test_youtube_acquire_needs_no_license_confirmation(client):
    app, settings, _ = client
    from twcorpus.core import create_source
    sid = create_source(settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t")
    form = {"subtitle_language": "zh-TW", "subtitle_kind": "uploader_provided"}
    queued = app.post(f"/api/sources/{sid}/acquire", data=form, follow_redirects=False)
    assert queued.status_code == 303 and queued.headers["location"].startswith(f"/sources/{sid}/fetch")
    assert app.post(f"/api/sources/{sid}/acquire", data={**form, "subtitle_language": "x"}).status_code == 400
    assert app.post(f"/api/sources/{sid}/edit", data={"license_label": "creative_commons", "license_evidence": "",
                                                  "audio_language": "zh", "speaker_count": "unknown",
                                                  "taiwan_basis": "unknown"}).status_code == 400


def test_guards_reject_cross_site_posts_bad_hosts_and_bad_ids(client):
    app, cfg_settings, _ = client
    assert app.post("/api/jobs/embedding", headers={"origin": "https://evil.example"}).status_code == 403
    assert app.post("/api/jobs/embedding", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert app.post("/api/jobs/embedding", headers={"origin": "null"}).status_code == 403
    assert app.post("/api/jobs/embedding", headers={"origin": "http://testserver"}, follow_redirects=False).status_code == 303
    # Real browser form post: Sec-Fetch-Site is authoritative even if Origin is "null" (no-referrer pages).
    browser = {"origin": "null", "sec-fetch-site": "same-origin"}
    assert app.post("/api/jobs/embedding", headers=browser, follow_redirects=False).status_code == 303
    # Every form in every page posts to /api/, so it is routed to the write endpoints.
    import re
    for path in PAGES:
        actions = re.findall(r'<form method="post" action="([^"]+)"', app.get(path).text)
        assert all(a.startswith("/api/") for a in actions), (path, actions)
    from twcorpus.core import create_source
    yt = create_source(cfg_settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t")
    for sub in SOURCE_SUBPAGES:
        page = app.get(f"/sources/{yt}{sub}")
        assert page.status_code == 200, sub
        assert all(a.startswith("/api/") for a in re.findall(r'<form method="post" action="([^"]+)"', page.text)), sub
    assert app.post("/sources/inspect", data={"urls": "x"}).status_code == 405
    jobs = app.get("/api/jobs").json()["jobs"]
    assert jobs and jobs[0]["kind"] == "embedding" and jobs[0]["status"] == "queued" and "payload_json" not in jobs[0]
    assert "statistics" in app.get("/api/stats").json()
    assert app.get("/", headers={"host": "attacker.example"}).status_code == 400
    assert app.get("/api/media/segments/..%2Fcorpus.sqlite3").status_code == 404
    assert app.get("/sources/not-an-id").status_code == 404
    assert app.post("/api/sources/inspect", data={"urls": "https://example.com/x"}).status_code == 400
    # Panel buttons use fetch with Accept: application/json and get JSON back instead of a redirect.
    job_id = jobs[0]["id"]
    cancel = app.post(f"/api/jobs/{job_id}/cancel", headers={"accept": "application/json"})
    assert cancel.status_code == 200 and cancel.json()["ok"]
    assert app.get("/api/jobs").json()["jobs"][0]["cancel_requested"] is True
    retry = app.post(f"/api/jobs/{job_id}/retry", headers={"accept": "application/json"})
    assert retry.status_code == 400 and "error" in retry.json()
    assert app.get("/api/jobs").headers["cache-control"] == "no-store"


def test_delete_source_removes_rows_and_files(client):
    app, settings, tmp_path = client
    audio, subtitle = make_media(tmp_path)
    with audio.open("rb") as a, subtitle.open("rb") as sub:
        app.post("/api/sources/local", files={"audio": ("a.wav", a, "audio/wav"), "subtitle": ("a.srt", sub, "text/plain")},
                 data={"title": "t", "language": "zh", "subtitle_language": "zh", "subtitle_kind": "local_provided",
                       "speaker_count": "single", "taiwan_basis": "unknown", "license_label": "unknown",
                       "license_evidence": ""})
    from twcorpus.jobs import run_one
    run_one(settings)
    sid = list_sources(settings)[0]["id"]
    assert 'action="/api/sources/%s/delete"' % sid in app.get("/sources").text
    assert 'action="/api/sources/%s/delete"' % sid not in app.get(f"/sources/{sid}").text
    assert 'action="/api/sources/%s/delete"' % sid in app.get(f"/sources/{sid}/manage").text
    assert app.post("/api/sources/%s/delete" % ("0" * 32), follow_redirects=False).status_code == 404
    gone = app.post(f"/api/sources/{sid}/delete", follow_redirects=False)
    assert gone.status_code == 303 and "notice=deleted" in gone.headers["location"]
    assert not list_sources(settings) and not list_segments(settings)
    assert app.get(f"/sources/{sid}").status_code == 404
    assert not [p for p in (settings.data_dir / "assets").rglob("*") if p.is_file()]
    for path in PAGES:
        assert app.get(path).status_code == 200, path


def test_playback_mp3_job_and_youtube_subtitle_only_route(client):
    app, settings, tmp_path = client
    from twcorpus.core import create_source, import_local
    from twcorpus.jobs import run_one
    audio, _ = make_media(tmp_path)
    sid = import_local(settings, audio, title="no subtitles")
    # Audio without subtitles still gets a player (MP3 made during import).
    page = app.get(f"/sources/{sid}").text
    assert f'src="/api/media/sources/{sid}"' in page
    assert "已有 MP3" in app.get(f"/sources/{sid}/audio").text
    assert app.get(f"/api/media/sources/{sid}").headers["content-type"] == "audio/mpeg"
    # Re-queueing the conversion is idempotent.
    assert app.post(f"/api/sources/{sid}/playback", follow_redirects=False).status_code == 303
    run_one(settings)
    assert app.get("/api/jobs").json()["jobs"][0]["status"] == "completed"

    yt = create_source(settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t")
    form = {"subtitle_language": "zh-TW", "subtitle_kind": "uploader_provided"}
    queued = app.post(f"/api/sources/{yt}/fetch-subtitles", data=form, follow_redirects=False)
    assert queued.status_code == 303
    assert app.get("/api/jobs").json()["jobs"][0]["kind_label"] == "YouTube 字幕"
    assert app.post(f"/api/sources/{yt}/playback").status_code == 400  # no original audio yet


def test_track_picker_refresh_and_srt_download(client):
    app, settings, tmp_path = client
    from twcorpus.core import create_source, import_local, add_subtitles, list_tracks
    from twcorpus.db import connect
    from twcorpus.core import dumps
    audio, subtitle = make_media(tmp_path)
    sid = import_local(settings, audio, subtitle, subtitle_language="zh", subtitle_kind="local_provided", title="x")
    track = list_tracks(settings, sid)[0]
    download = app.get(f"/api/media/tracks/{track['id']}")
    assert download.status_code == 200 and "-->" in download.text
    assert app.get("/api/media/tracks/" + "0" * 32).status_code == 404
    assert f'href="/api/media/tracks/{track["id"]}"' in app.get(f"/sources/{sid}/tracks").text

    yt = create_source(settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t")
    tracks = [{"language": "zh-TW", "kind": "uploader_provided", "formats": ["vtt"]},
              {"language": "en", "kind": "auto_generated", "formats": ["vtt"]}]
    update_source(settings, yt, license_label="creative_commons", license_evidence="CC", metadata_json=dumps({"tracks": tracks}))
    page = app.get(f"/sources/{yt}/fetch").text
    assert 'name="track"' in page and 'value="uploader_provided|zh-TW"' in page and 'value="auto_generated|en"' in page
    # Picking from the list sends "kind|language"; the queued job carries them separately.
    app.post(f"/api/sources/{yt}/fetch-subtitles", data={"track": "auto_generated|en"})
    import json as _json
    with connect(settings.database) as db:
        payload = _json.loads(db.execute("SELECT payload_json FROM jobs WHERE kind='youtube_subtitles'").fetchone()[0])
    assert payload["subtitle_language"] == "en" and payload["subtitle_kind"] == "auto_generated"
    refreshed = app.post(f"/api/sources/{yt}/refresh-tracks", follow_redirects=False)
    assert refreshed.status_code == 303 and "jobs=open" in refreshed.headers["location"]
    assert app.post(f"/api/sources/{sid}/refresh-tracks").status_code == 400  # local source


def test_reprocess_local_rebuilds_derived_files_and_youtube_needs_a_track(client):
    app, settings, tmp_path = client
    from twcorpus.core import create_source, import_local
    from twcorpus.jobs import run_one
    audio, subtitle = make_media(tmp_path)
    sid = import_local(settings, audio, subtitle, subtitle_language="zh", subtitle_kind="local_provided", title="x")
    assert f'action="/api/sources/{sid}/reprocess"' in app.get(f"/sources/{sid}/fetch").text
    before = {s["id"] for s in list_segments(settings)}
    # Drop the derived MP3 and WAV files; reprocessing must recreate them from the stored original.
    from twcorpus.core import asset_path
    from twcorpus.db import connect
    with connect(settings.database) as db:
        derived = db.execute("SELECT path FROM assets WHERE source_id=? AND kind IN ('playback_audio','normalized_audio')", (sid,)).fetchall()
        original = db.execute("SELECT path FROM assets WHERE source_id=? AND kind='original_audio'", (sid,)).fetchone()["path"]
    for row in derived:
        # A 16 kHz mono WAV upload normalizes to identical bytes, so both assets share one stored file.
        if row["path"] != original:
            asset_path(settings, row["path"]).unlink()
    assert app.post(f"/api/sources/{sid}/reprocess", follow_redirects=False).status_code == 303
    assert app.get("/api/jobs").json()["jobs"][0]["kind_label"] == "重新處理"
    run_one(settings)
    job = app.get("/api/jobs").json()["jobs"][0]
    assert job["status"] == "completed", job
    assert app.get(f"/api/media/sources/{sid}").headers["content-type"] == "audio/mpeg"
    assert {s["id"] for s in list_segments(settings)} == before
    assert app.post("/api/sources/" + "0" * 32 + "/reprocess").status_code == 404

    yt = create_source(settings, "youtube", "abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijk", "t")
    form = {"track": "uploader_provided|zh-TW"}
    assert app.post(f"/api/sources/{yt}/reprocess").status_code == 400  # nothing to repeat
    assert app.post(f"/api/sources/{yt}/reprocess", data=form, follow_redirects=False).status_code == 303


def test_source_list_filters_by_domain_and_imports_open_in_dialogs(client):
    app, settings, _ = client
    from twcorpus.core import create_source
    news = create_source(settings, "youtube", "aaaaaaaaaaa", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "晚間新聞", domain="news")
    food = create_source(settings, "youtube", "bbbbbbbbbbb", "https://www.youtube.com/watch?v=bbbbbbbbbbb", "夜市美食", domain="lifestyle")
    page = app.get("/sources").text
    assert "晚間新聞" in page and "夜市美食" in page
    assert page.count("<dialog") == 3 and 'data-dialog-open="dlg-local"' in page and "data-open-on-load" not in page
    only_news = app.get("/sources?domain=news").text
    assert "晚間新聞" in only_news and "夜市美食" not in only_news
    assert "domain=lifestyle" in only_news, "other domains stay reachable while one is chosen"
    assert "夜市" in app.get("/sources?q=夜市").text and "晚間新聞" not in app.get("/sources?q=夜市").text
    assert "沒有符合篩選" in app.get("/sources?domain=sports").text
    assert "data-open-on-load" in app.get("/sources?tab=urls").text
    bad = app.post("/api/sources/inspect", data={"urls": "https://example.com/x"})
    assert bad.status_code == 400 and "data-open-on-load" in bad.text and "操作未完成" in bad.text
    edit = app.post(f"/api/sources/{food}/edit", data={"license_label": "unknown", "license_evidence": "", "audio_language": "zh",
                                                    "speaker_count": "unknown", "taiwan_basis": "unknown", "domain": "travel"})
    assert edit.status_code in (200, 303)
    assert "domain=travel" in app.get("/sources").text
    assert app.post(f"/api/sources/{food}/edit", data={"license_label": "unknown", "license_evidence": "", "audio_language": "zh",
                                                    "speaker_count": "unknown", "taiwan_basis": "unknown", "domain": "bogus"}).status_code == 400
