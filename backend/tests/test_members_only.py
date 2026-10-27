"""Members-only videos are marked unsupported (skipped) and never downloaded."""
from __future__ import annotations


def test_mark_if_members_only():
    from voti.models import Media
    from voti.sources.handlers import mark_if_members_only
    m = Media(platform_media_id="x", status="listed", availability="subscriber_only")
    assert mark_if_members_only(m) is True
    assert m.status == "skipped" and m.failure_code == "members_only"
    ok = Media(platform_media_id="y", status="listed", availability="public")
    assert mark_if_members_only(ok) is False and ok.status == "listed"
    done = Media(platform_media_id="z", status="ready", availability="premium_only")
    assert mark_if_members_only(done) is True and done.status == "ready"  # never moves processed media backwards


def test_download_skips_members_only(admin_client, factory):
    ch = factory.channel()
    a = factory.media(channel_id=ch.id, status="detailed", availability="subscriber_only",
                      has_manual_captions=True, manual_caption_langs=["zh-TW"])
    b = factory.media(channel_id=ch.id, status="detailed", has_manual_captions=True, manual_caption_langs=["zh-TW"])
    r = admin_client.post("/api/media/download", json={"media_ids": [str(a.id), str(b.id)]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1 and r.json()["skipped_reasons"] == {"members_only": 1}


def test_members_only_job_failure_is_skipped():
    from voti import queue
    assert "members_only" in queue.SKIP_CODES
