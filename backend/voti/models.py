"""Database schema — the shared contract between API, workers and pipeline modules.

Conventions
- UUID primary keys (uuid4) except tiny lookup tables.
- All timestamps are timezone-aware UTC.
- Audio and large arrays live in object storage (voti.storage); rows hold the S3 key in `Asset.key`.
- Segments are timestamps into the media's normalized audio; no per-segment audio objects are stored.
- `platform` columns keep room for non-YouTube sources ("youtube" today; "upload", "podcast", … later).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 192  # speechbrain/spkrec-ecapa-voxceleb


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSONB, list: JSONB, datetime: DateTime(timezone=True)}


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)


def fk(target: str, nullable: bool = False, ondelete: str = "CASCADE", index: bool = True) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=index)


def created() -> Mapped[datetime]:
    return mapped_column(default=utcnow)


# ───────────────────────────── accounts ─────────────────────────────

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="annotator")  # admin | annotator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 hex of the cookie token
    user_id: Mapped[uuid.UUID] = fk("users.id")
    created_at: Mapped[datetime] = created()
    expires_at: Mapped[datetime] = mapped_column()
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Runtime-editable settings (admin page). Known keys are documented in docs/ARCHITECTURE.md."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = created()


# ───────────────────────────── sources ─────────────────────────────

class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("platform", "platform_channel_id"),)
    id: Mapped[uuid.UUID] = pk()
    platform: Mapped[str] = mapped_column(String(16), default="youtube")
    platform_channel_id: Mapped[str] = mapped_column(String(64))      # e.g. UCxxxx
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)  # @name
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    banner_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)  # everything else from the extractor
    notes: Mapped[str] = mapped_column(Text, default="")                      # human notes (speaker diversity etc.)
    # new (never queued) → queued → listing → listed | failed. Follows the channel.list job (voti.queue
    # _sync_entities; voti.sources.handlers.reconcile_channel_status); the API derives it from the active job.
    list_status: Mapped[str] = mapped_column(String(16), default="new")
    last_listed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created()


class Playlist(Base):
    __tablename__ = "playlists"
    __table_args__ = (UniqueConstraint("platform", "platform_playlist_id"),)
    id: Mapped[uuid.UUID] = pk()
    platform: Mapped[str] = mapped_column(String(16), default="youtube")
    platform_playlist_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[uuid.UUID | None] = fk("channels.id", nullable=True, ondelete="SET NULL")
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    list_status: Mapped[str] = mapped_column(String(16), default="new")
    last_listed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created()


class Media(Base):
    """One video (or, later, one uploaded file / podcast episode).

    status: listed → detailed → queued → downloading → downloaded → processing → ready
            (any) → failed | blocked | skipped
    detail fields (captions, license, …) are filled by the `media.detail` job; listing alone only
    gives id/title/duration/kind.
    """
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("platform", "platform_media_id"),
                      Index("media_channel_published", "channel_id", "published_at"))
    id: Mapped[uuid.UUID] = pk()
    platform: Mapped[str] = mapped_column(String(16), default="youtube")
    platform_media_id: Mapped[str] = mapped_column(String(64))
    channel_id: Mapped[uuid.UUID | None] = fk("channels.id", nullable=True, ondelete="SET NULL", index=False)
    url: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="video")  # video | short | live | upload
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    categories: Mapped[list] = mapped_column(JSONB, default=list)
    chapters: Mapped[list] = mapped_column(JSONB, default=list)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)   # declared audio/default language
    license: Mapped[str | None] = mapped_column(String(32), nullable=True)    # "youtube" (standard) | "creative_commons"
    availability: Mapped[str | None] = mapped_column(String(32), nullable=True)
    age_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # caption availability (detail job). manual = uploader provided ("native CC"); auto = YouTube ASR
    has_manual_captions: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    has_auto_captions: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    manual_caption_langs: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    auto_caption_langs: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)  # selected extra fields
    info_json_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL", index=False)
    status: Mapped[str] = mapped_column(String(16), default="listed", index=True)
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)      # current pipeline step name
    failure_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detailed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # set when a re-listing (scheduled or manual refresh, not the channel's first listing) found this video
    discovered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class PlaylistItem(Base):
    __tablename__ = "playlist_items"
    playlist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("playlists.id", ondelete="CASCADE"),
                                                   primary_key=True)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"),
                                                primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Asset(Base):
    """An object in RustFS. kind:
    info_json | original_audio | normalized_audio (16 kHz mono wav) | subtitle |
    frames_quality (npz: brouhaha vad/snr/c50, silero vad, per-frame) | fingerprint | export_archive | export_manifest
    """
    __tablename__ = "assets"
    __table_args__ = (Index("assets_media_kind", "media_id", "kind"),)
    id: Mapped[uuid.UUID] = pk()
    media_id: Mapped[uuid.UUID | None] = fk("media.id", nullable=True, index=False)
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(64), default="application/octet-stream")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)  # tool versions, upstream asset ids, ffmpeg args
    created_at: Mapped[datetime] = created()


class Track(Base):
    """Subtitle track. kind: manual | auto | translated | uploaded"""
    __tablename__ = "tracks"
    id: Mapped[uuid.UUID] = pk()
    media_id: Mapped[uuid.UUID] = fk("media.id")
    language: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    format: Mapped[str] = mapped_column(String(8))  # vtt | srt | json3
    asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL", index=False)
    created_at: Mapped[datetime] = created()


class Cue(Base):
    __tablename__ = "cues"
    __table_args__ = (UniqueConstraint("track_id", "idx"),)
    id: Mapped[uuid.UUID] = pk()
    track_id: Mapped[uuid.UUID] = fk("tracks.id", index=False)
    idx: Mapped[int] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    raw_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)        # cleaned (+ Simplified → Traditional when flag s2t_converted)
    flags: Mapped[list] = mapped_column(JSONB, default=list)


# ───────────────────────────── analysis ─────────────────────────────

class MediaQuality(Base):
    """Per-media audio quality summary. Per-frame arrays are in the frames_quality asset."""
    __tablename__ = "media_quality"
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.id", ondelete="CASCADE"),
                                                primary_key=True)
    # container / stream (ffprobe on the ORIGINAL download)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # spectral (ORIGINAL audio, not the 16 kHz copy)
    real_bandwidth_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    upsampled_suspect: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # loudness / clipping
    lufs_integrated: Mapped[float | None] = mapped_column(Float, nullable=True)
    true_peak_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    clipping_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # speech (brouhaha VAD primary, silero cross-check)
    speech_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    speech_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    vad_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)  # frame agreement brouhaha vs silero
    snr_mean: Mapped[float | None] = mapped_column(Float, nullable=True)       # over speech frames, dB
    snr_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    c50_mean: Mapped[float | None] = mapped_column(Float, nullable=True)       # dB
    # DNSMOS (averaged over speech)
    dnsmos_sig: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_bak: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_ovrl: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_p808: Mapped[float | None] = mapped_column(Float, nullable=True)
    frames_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL", index=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # histograms, model versions, spectrum summary
    computed_at: Mapped[datetime] = created()


class SpeechRegion(Base):
    """Merged VAD speech intervals. source: brouhaha | silero"""
    __tablename__ = "speech_regions"
    __table_args__ = (Index("speech_regions_media", "media_id", "source", "start_sec"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    media_id: Mapped[uuid.UUID] = fk("media.id", index=False)
    source: Mapped[str] = mapped_column(String(16))
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    snr_mean: Mapped[float | None] = mapped_column(Float, nullable=True)


class DiarizationTurn(Base):
    """pyannote output. local_speaker is only unique within one media (e.g. SPEAKER_00)."""
    __tablename__ = "diarization_turns"
    __table_args__ = (Index("diarization_media", "media_id", "start_sec"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    media_id: Mapped[uuid.UUID] = fk("media.id", index=False)
    local_speaker: Mapped[str] = mapped_column(String(32))
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    is_overlap: Mapped[bool] = mapped_column(Boolean, default=False)
    model_id: Mapped[str] = mapped_column(String(128))


class RepeatedRegion(Base):
    """Audio repeated across media of the same channel (intro/outro/jingle), found by fingerprinting."""
    __tablename__ = "repeated_regions"
    __table_args__ = (Index("repeated_media", "media_id", "start_sec"),)
    id: Mapped[uuid.UUID] = pk()
    channel_id: Mapped[uuid.UUID | None] = fk("channels.id", nullable=True)
    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)  # same content across media
    media_id: Mapped[uuid.UUID] = fk("media.id", index=False)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    position: Mapped[str] = mapped_column(String(16), default="middle")  # intro | outro | middle
    match_count: Mapped[int] = mapped_column(Integer, default=0)          # other media sharing this group
    score: Mapped[float] = mapped_column(Float, default=0.0)
    excluded: Mapped[bool] = mapped_column(Boolean, default=True)         # human can un-exclude
    created_at: Mapped[datetime] = created()


class Segment(Base):
    """A unit of the corpus: [start_sec, end_sec) on the media's normalized audio.

    text_source: manual_caption | auto_caption | uploaded | none
    review_status: unchecked | passed | needs_fix | excluded
    speaker_state: unknown | single | multiple | overlap
    flags (list[str]): low_speech, low_snr, clipped, repeated, overlap, boundary_adjusted, too_short, too_long,
                       s2t_converted (original_text converted Simplified → Traditional, voti.textconv), …
    original_text is never edited by humans; the only automatic rewrite is that s2t conversion.
    """
    __tablename__ = "segments"
    __table_args__ = (Index("segments_media_start", "media_id", "start_sec"),
                      Index("segments_review", "review_status", "active"))
    id: Mapped[uuid.UUID] = pk()
    media_id: Mapped[uuid.UUID] = fk("media.id", index=False)
    track_id: Mapped[uuid.UUID | None] = fk("tracks.id", nullable=True, ondelete="SET NULL", index=False)
    cue_ids: Mapped[list] = mapped_column(JSONB, default=list)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    original_text: Mapped[str] = mapped_column(Text, default="")   # caption text (only s2t conversion rewrites it)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_source: Mapped[str] = mapped_column(String(16), default="none")
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    local_speaker: Mapped[str | None] = mapped_column(String(32), nullable=True)  # dominant diarization label
    speaker_state: Mapped[str] = mapped_column(String(16), default="unknown")
    # quality snapshot over the segment span
    speech_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    snr: Mapped[float | None] = mapped_column(Float, nullable=True)
    c50: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_sig: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_bak: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_ovrl: Mapped[float | None] = mapped_column(Float, nullable=True)
    dnsmos_p808: Mapped[float | None] = mapped_column(Float, nullable=True)
    clipping_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    lufs: Mapped[float | None] = mapped_column(Float, nullable=True)
    repeated_overlap: Mapped[float] = mapped_column(Float, default=0.0)  # fraction covered by excluded RepeatedRegion
    flags: Mapped[list] = mapped_column(JSONB, default=list)
    review_status: Mapped[str] = mapped_column(String(16), default="unchecked")
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)  # bumps when boundaries change
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("segment_id", "revision", "model_id"),)
    id: Mapped[uuid.UUID] = pk()
    segment_id: Mapped[uuid.UUID] = fk("segments.id")
    revision: Mapped[int] = mapped_column(Integer)
    model_id: Mapped[str] = mapped_column(String(128))
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = created()


class WordAlignment(Base):
    """Character/word timing of one segment's text (`media.align`, voti.pipeline.align), one row per segment.

    text = corrected_text if set else original_text; the row is valid only while revision == Segment.revision and
    text_hash == voti.pipeline.align.text_hash(text). status: aligned | skipped (no usable romanizer, e.g. nan/hak,
    or no text; `reason`) | failed (`reason`) | pending (a re-alignment was requested after a text/boundary edit).
    units: [[unit, start_sec|null, end_sec|null, score|null], ...] in text order; "".join(unit) == text.
    """
    __tablename__ = "word_alignments"
    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("segments.id", ondelete="CASCADE"),
                                                  primary_key=True)
    media_id: Mapped[uuid.UUID] = fk("media.id")
    revision: Mapped[int] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="aligned")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str] = mapped_column(String(64), default="")
    units: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class VoiceProfile(Base):
    """Vox-Profile predictions for one segment revision.

    Each JSON field is {"label": str, "probs": {label: p}} or, for dimensional emotion,
    {"arousal": x, "valence": y, "dominance": z}. Accent models are English-only (reference only).
    """
    __tablename__ = "voice_profiles"
    __table_args__ = (UniqueConstraint("segment_id", "revision", "backbone"),)
    id: Mapped[uuid.UUID] = pk()
    segment_id: Mapped[uuid.UUID] = fk("segments.id")
    revision: Mapped[int] = mapped_column(Integer)
    backbone: Mapped[str] = mapped_column(String(32))  # whisper-large-v3 | wavlm-large
    age: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_group: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sex_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    voice_quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    emotion: Mapped[dict] = mapped_column(JSONB, default=dict)
    emotion_dim: Mapped[dict] = mapped_column(JSONB, default=dict)
    speech_flow: Mapped[dict] = mapped_column(JSONB, default=dict)
    accent_broad: Mapped[dict] = mapped_column(JSONB, default=dict)
    accent_narrow: Mapped[dict] = mapped_column(JSONB, default=dict)
    model_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created()


class ClusterRun(Base):
    __tablename__ = "cluster_runs"
    id: Mapped[uuid.UUID] = pk()
    model_id: Mapped[str] = mapped_column(String(128))
    algorithm: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float] = mapped_column(Float)
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)   # {"channel_ids": [...]} or {} for all
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    created_at: Mapped[datetime] = created()


class Speaker(Base):
    """Candidate speaker = one cluster of one run. Not a confirmed identity until a human names it."""
    __tablename__ = "speakers"
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = fk("cluster_runs.id")
    label: Mapped[str] = mapped_column(String(32))                  # cand_001
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sec: Mapped[float] = mapped_column(Float, default=0.0)
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    channel_ids: Mapped[list] = mapped_column(JSONB, default=list)
    centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)      # aggregated Vox-Profile (age/sex dist …)
    created_at: Mapped[datetime] = created()


class SpeakerAssignment(Base):
    __tablename__ = "speaker_assignments"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cluster_runs.id", ondelete="CASCADE"),
                                              primary_key=True)
    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("segments.id", ondelete="CASCADE"),
                                                  primary_key=True)
    speaker_id: Mapped[uuid.UUID] = fk("speakers.id")
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)


class SpeakerOverride(Base):
    """Human assignment of a segment to a named speaker; survives re-clustering."""
    __tablename__ = "speaker_overrides"
    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("segments.id", ondelete="CASCADE"),
                                                  primary_key=True)
    speaker_name: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ───────────────────────────── datasets ─────────────────────────────

class Dataset(Base):
    """Saved filter definition. filters schema: see docs/ARCHITECTURE.md#dataset-filters."""
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class DatasetExport(Base):
    """Frozen snapshot of a dataset. status: queued | running | completed | failed"""
    __tablename__ = "dataset_exports"
    id: Mapped[uuid.UUID] = pk()
    dataset_id: Mapped[uuid.UUID] = fk("datasets.id")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sec: Mapped[float] = mapped_column(Float, default=0.0)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    manifest_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL", index=False)
    archive_asset_id: Mapped[uuid.UUID | None] = fk("assets.id", nullable=True, ondelete="SET NULL", index=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    created_at: Mapped[datetime] = created()


# ───────────────────────────── jobs ─────────────────────────────

class Job(Base):
    """Persistent job record; Redis only carries job ids (see voti.queue).

    status: queued → running → completed | failed | blocked | cancelled | interrupted
    A running job can go back to queued: worker shutdown (attempt not counted), orphan recovery
    (phase "recovered") or a transient error (phase "retry_wait", run_after = earliest next start).
    Columns added after the first release are also added by voti.db.init_db (ALTER TABLE … IF NOT EXISTS).
    """
    __tablename__ = "jobs"
    __table_args__ = (Index("jobs_status_created", "status", "created_at"),
                      Index("jobs_active_kind_media", "kind", "media_id", unique=True,
                            postgresql_where="status IN ('queued','running') AND media_id IS NOT NULL"),
                      Index("jobs_running_heartbeat", "heartbeat_at", postgresql_where="status = 'running'"))
    id: Mapped[uuid.UUID] = pk()
    kind: Mapped[str] = mapped_column(String(48))
    queue: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    media_id: Mapped[uuid.UUID | None] = fk("media.id", nullable=True)
    channel_id: Mapped[uuid.UUID | None] = fk("channels.id", nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[float] = mapped_column(Float, default=0.0)   # 0..1
    phase: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)  # first run + automatic retries
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    worker: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True, ondelete="SET NULL", index=False)
    created_at: Mapped[datetime] = created()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    run_after: Mapped[datetime | None] = mapped_column(nullable=True)  # not claimable before (retry backoff)
