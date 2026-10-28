"""Channel deletion: impact preview, confirmation for downloaded data, cascade + object cleanup."""
from __future__ import annotations

from sqlalchemy import func, select


def _corpus(factory, db):  # noqa: ANN001, ANN202
    from voti import storage
    from voti.models import Asset, ClusterRun, Job, Playlist, Speaker, SpeakerAssignment
    ch, other = factory.channel(title="呆呆伙伴"), factory.channel(title="別的頻道")
    dl = factory.media(channel_id=ch.id, duration_sec=120.0, status="ready")
    factory.media(channel_id=ch.id, status="listed")
    keep = factory.media(channel_id=other.id, status="ready")
    for m in (dl, keep):
        key = storage.media_key(m.id, "normalized.wav")
        storage.put_bytes(b"RIFF....", key, "audio/wav")
        factory.add(Asset(media_id=m.id, kind="normalized_audio", key=key, sha256="0" * 64, bytes=8))
    a = factory.segment(dl, 0, 2, review_status="passed")
    factory.segment(dl, 2, 4)
    b = factory.segment(keep, 0, 3)
    factory.add(Playlist(platform_playlist_id="PL1", channel_id=ch.id, title="p", url="u"))
    factory.add(Job(kind="media.embed", queue="ml", media_id=dl.id, status="queued"))
    run = factory.add(ClusterRun(model_id="m", algorithm="a", threshold=0.6, is_current=True))
    sp_gone = factory.add(Speaker(run_id=run.id, label="cand_001", segment_count=1, total_sec=2.0))
    sp_mixed = factory.add(Speaker(run_id=run.id, label="cand_002", segment_count=2, total_sec=5.0))
    db.add_all([SpeakerAssignment(run_id=run.id, segment_id=a.id, speaker_id=sp_gone.id),
                SpeakerAssignment(run_id=run.id, segment_id=b.id, speaker_id=sp_mixed.id)])
    db.commit()
    return ch, other, dl, keep, sp_gone, sp_mixed


def test_impact_and_confirmation_required(admin_client, factory, db):
    ch, *_ = _corpus(factory, db)
    r = admin_client.get(f"/api/channels/{ch.id}/delete-impact")
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["media_total"] == 2 and imp["downloaded_media"] == 1 and imp["downloaded_sec"] == 120.0
    assert imp["segments"] == 2 and imp["reviewed_segments"] == 1 and imp["playlists"] == 1
    assert imp["active_jobs"] == 1 and imp["storage_bytes"] == 8 and imp["requires_confirmation"] is True
    r = admin_client.delete(f"/api/channels/{ch.id}")
    assert r.status_code == 409 and r.json()["code"] == "confirmation_required"
    assert admin_client.delete(f"/api/channels/{ch.id}", params={"confirm": "錯的"}).status_code == 409


def test_delete_cascades_and_keeps_other_channel(admin_client, factory, db):
    from voti import storage
    from voti.models import AuditLog, Channel, Job, Media, Playlist, Segment, Speaker
    objs = _corpus(factory, db)
    ch_id, other_id, dl_id, keep_id, gone_id, mixed_id = (o.id for o in objs)
    r = admin_client.delete(f"/api/channels/{ch_id}", params={"confirm": "呆呆伙伴"})
    assert r.status_code == 200, r.text
    assert r.json()["objects_deleted"] == 1
    db.expire_all()
    assert db.get(Channel, ch_id) is None and db.get(Channel, other_id) is not None
    assert db.scalar(select(func.count()).select_from(Media).where(Media.id == dl_id)) == 0
    assert db.scalar(select(func.count()).select_from(Segment).where(Segment.media_id == dl_id)) == 0
    assert db.scalar(select(func.count()).select_from(Segment).where(Segment.media_id == keep_id)) == 1
    assert db.scalar(select(func.count()).select_from(Playlist)) == 0
    assert db.scalar(select(func.count()).select_from(Job).where(Job.media_id == dl_id)) == 0
    assert storage.head(storage.media_key(dl_id, "normalized.wav")) is None
    assert storage.head(storage.media_key(keep_id, "normalized.wav")) is not None
    assert db.get(Speaker, gone_id) is None                      # its only segment was deleted
    mixed = db.get(Speaker, mixed_id)
    assert mixed.segment_count == 1 and mixed.total_sec == 3.0 and mixed.channel_ids == [str(other_id)]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "channel.delete")) is not None


def test_listed_only_channel_needs_no_confirmation(admin_client, factory):
    ch = factory.channel(title="只列出")
    factory.media(channel_id=ch.id, status="listed")
    assert admin_client.get(f"/api/channels/{ch.id}/delete-impact").json()["requires_confirmation"] is False
    assert admin_client.delete(f"/api/channels/{ch.id}").status_code == 200


def test_annotator_cannot_delete(annotator_client, factory):
    ch = factory.channel()
    assert annotator_client.delete(f"/api/channels/{ch.id}").status_code == 403
    assert annotator_client.get(f"/api/channels/{ch.id}/delete-impact").status_code == 403


def test_delete_video_clears_processed_data_and_keeps_the_video(admin_client, factory, db):
    from voti import storage
    from voti.models import Asset, AuditLog, Job, Media, Segment, Speaker, Track
    objs = _corpus(factory, db)
    ch_id, other_id, dl_id, keep_id, gone_id, mixed_id = (o.id for o in objs)
    info = storage.media_key(dl_id, "info.json")
    storage.put_bytes(b"{}", info, "application/json")
    factory.add(Asset(media_id=dl_id, kind="info_json", key=info, sha256="1" * 64, bytes=2))
    factory.add(Track(media_id=dl_id, kind="manual", language="zh-TW", format="json3"))
    m = db.get(Media, dl_id)
    m.detailed_at, m.metadata_ = m.created_at, {"download": {"x": 1}, "ocr": {"status": "done"}, "keep": 1}
    db.commit()
    listed = db.scalar(select(Media.id).where(Media.channel_id == ch_id, Media.status == "listed"))
    imp = admin_client.post("/api/media/delete-impact", json={"media_ids": [str(dl_id), str(listed)]}).json()
    assert imp["media_total"] == 1 and imp["unprocessed"] == 1 and imp["downloaded_media"] == 1
    assert imp["reviewed_segments"] == 1 and imp["requires_confirmation"] is True and imp["storage_bytes"] == 8
    r = admin_client.post("/api/media/delete", json={"media_ids": [str(dl_id)]})
    assert r.status_code == 409 and r.json()["code"] == "confirmation_required"
    r = admin_client.post("/api/media/delete", json={"media_ids": [str(dl_id), str(listed)], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["objects_deleted"] == 1 and r.json()["media_total"] == 1
    db.expire_all()
    m = db.get(Media, dl_id)
    assert m is not None and m.channel_id == ch_id and m.status == "detailed" and m.stage is None
    assert m.metadata_ == {"keep": 1}
    assert db.get(Media, listed).status == "listed"  # never processed: untouched
    for model in (Segment, Track, Job):
        assert db.scalar(select(func.count()).select_from(model).where(model.media_id == dl_id)) == 0
    assert [a.kind for a in db.scalars(select(Asset).where(Asset.media_id == dl_id))] == ["info_json"]
    assert storage.head(storage.media_key(dl_id, "normalized.wav")) is None and storage.head(info) is not None
    assert storage.head(storage.media_key(keep_id, "normalized.wav")) is not None
    assert db.get(Speaker, gone_id) is None and db.get(Speaker, mixed_id).segment_count == 1
    assert db.scalar(select(AuditLog).where(AuditLog.action == "media.clear")) is not None
    # the video can be downloaded again
    r = admin_client.post("/api/media/download", json={"media_ids": [str(dl_id)]})
    assert r.status_code == 200 and r.json()["queued"] == 1


def test_delete_videos_by_filter_and_permissions(admin_client, annotator_client, factory, db):
    from voti.models import Media
    ch = factory.channel(title="批次")
    ms = [factory.media(channel_id=ch.id, status=st).id for st in ("ready", "skipped", "listed")]
    body = {"filter": {"channel_id": str(ch.id)}}
    assert annotator_client.post("/api/media/delete-impact", json=body).status_code == 403
    assert annotator_client.post("/api/media/delete", json=body).status_code == 403
    assert admin_client.post("/api/media/delete", json={}).status_code == 400
    imp = admin_client.post("/api/media/delete-impact", json=body).json()
    assert imp["media_total"] == 1 and imp["unprocessed"] == 2 and imp["requires_confirmation"] is False
    assert admin_client.post("/api/media/delete", json=body).status_code == 200  # nothing downloaded: no confirm
    db.expire_all()
    assert [db.get(Media, i).status for i in ms] == ["listed", "skipped", "listed"]
