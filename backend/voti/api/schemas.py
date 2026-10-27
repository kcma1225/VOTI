"""Pydantic request/response models (the OpenAPI at /api/openapi.json is generated from these)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


# ───────────────────────────── auth & users ─────────────────────────────

Role = Literal["admin", "annotator"]


class UserOut(ORM):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class UserCreateIn(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.\-]{2,64}$")
    display_name: str = Field(default="", max_length=128)
    password: str
    role: Role = "annotator"


class UserPatchIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None


# ───────────────────────────── settings ─────────────────────────────

class YoutubeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_limit_kbps: int = Field(default=0, ge=0)
    sleep_between_downloads_sec: float = Field(default=5, ge=0, le=3600)
    max_concurrent_downloads: int = Field(default=1, ge=1, le=16)
    cookies_enabled: bool = False
    sleep_between_requests_sec: float = Field(default=2.0, ge=0, le=3600)  # listing/detail job spacing


class PipelineSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voxprofile_backbone: Literal["whisper-large-v3", "wavlm-large"] = "whisper-large-v3"
    diarization_enabled: bool = True
    segment_min_sec: float = Field(default=1.0, ge=0, le=60)
    segment_max_sec: float = Field(default=20.0, gt=0, le=120)
    edge_pad_sec: float = Field(default=0.1, ge=0, le=2)


class QualityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    low_speech_ratio: float = Field(default=0.5, ge=0, le=1)
    low_snr_db: float = 10
    clipping_ratio: float = Field(default=0.001, ge=0, le=1)


class TextSettings(BaseModel):
    """Caption text normalisation (voti.textconv)."""
    model_config = ConfigDict(extra="forbid")
    convert_simplified: bool = True                               # Simplified → Traditional (Taiwan) on ingestion
    opencc_config: Literal["s2twp", "s2tw", "s2t"] = "s2twp"      # OpenCC config


class CookiesStatus(BaseModel):
    present: bool
    updated_at: datetime | None = None


class SettingsOut(BaseModel):
    youtube: YoutubeSettings
    pipeline: PipelineSettings
    quality_thresholds: QualityThresholds
    text: TextSettings = TextSettings()
    cookies: CookiesStatus


# ───────────────────────────── jobs ─────────────────────────────

class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    queue: str
    status: str
    progress: float
    phase: str
    message: str
    error_code: str | None = None
    error_message: str | None = None
    attempts: int
    media_id: uuid.UUID | None = None
    channel_id: uuid.UUID | None = None
    media_title: str | None = None
    channel_title: str | None = None
    payload: dict = {}
    result: dict = {}
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    eta_sec: float | None = None
    run_after: datetime | None = None  # queued for an automatic retry: not started before this time


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    counts: dict[str, int]


class QueuedOut(BaseModel):
    queued: int


class DownloadOut(BaseModel):
    queued: int
    skipped: int
    skipped_reasons: dict[str, int] = {}


# ───────────────────────────── channels / playlists ─────────────────────────────

class ChannelCounts(BaseModel):
    listed: int = 0
    detailed: int = 0
    with_manual_captions: int = 0
    downloaded: int = 0
    ready: int = 0
    failed: int = 0


class ChannelHours(BaseModel):
    listed: float = 0.0
    ready: float = 0.0
    speech: float = 0.0


class ChannelSummary(BaseModel):
    id: uuid.UUID
    platform: str
    platform_channel_id: str
    title: str
    handle: str | None = None
    url: str
    thumbnail_url: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    list_status: str
    last_listed_at: datetime | None = None
    counts: ChannelCounts
    hours: ChannelHours


class ChannelSpeakers(BaseModel):
    candidates: int = 0
    total_sec: float = 0.0


class ChannelQuality(BaseModel):
    snr_mean: float | None = None
    dnsmos_ovrl: float | None = None
    c50_mean: float | None = None
    speech_ratio: float | None = None
    real_bandwidth_hz: float | None = None
    media_with_quality: int = 0


class ChannelDetail(ChannelSummary):
    description: str = ""
    banner_url: str | None = None
    country: str | None = None
    tags: list = []
    notes: str = ""
    metadata: dict = {}
    created_at: datetime | None = None
    speakers: ChannelSpeakers
    quality: ChannelQuality
    kind_counts: dict[str, int] = {}
    playlist_count: int = 0


class ChannelPatchIn(BaseModel):
    notes: str = Field(max_length=100_000)


class ChannelDeleteImpact(BaseModel):
    media_total: int
    playlists: int
    downloaded_media: int
    downloaded_sec: float
    segments: int
    reviewed_segments: int
    storage_bytes: int
    active_jobs: int
    datasets_referencing: int
    requires_confirmation: bool  # true → DELETE must carry ?confirm=<exact channel title>


class ChannelDeleteOut(ChannelDeleteImpact):
    objects_deleted: int = 0


class ChannelDetailIn(BaseModel):
    media_ids: list[uuid.UUID] | None = None


class PlaylistOut(BaseModel):
    id: uuid.UUID
    platform: str
    platform_playlist_id: str
    channel_id: uuid.UUID | None = None
    title: str
    description: str = ""
    url: str
    list_status: str
    last_listed_at: datetime | None = None
    created_at: datetime | None = None
    media_count: int = 0
    thumbnail_url: str | None = None


# ───────────────────────────── media ─────────────────────────────

class ChannelRef(BaseModel):
    id: uuid.UUID
    title: str


class MediaQualityBrief(BaseModel):
    snr_mean: float | None = None
    dnsmos_ovrl: float | None = None
    real_bandwidth_hz: float | None = None
    speech_ratio: float | None = None
    sample_rate: int | None = None
    upsampled_suspect: bool | None = None


class MediaSummary(BaseModel):
    id: uuid.UUID
    platform: str
    platform_media_id: str
    url: str
    kind: str
    title: str
    thumbnail_url: str | None = None
    duration_sec: float | None = None
    published_at: datetime | None = None
    view_count: int | None = None
    license: str | None = None
    has_manual_captions: bool | None = None
    has_auto_captions: bool | None = None
    manual_caption_langs: list[str] = []
    auto_caption_langs: list[str] = []
    status: str
    stage: str | None = None
    failure_code: str | None = None
    detailed_at: datetime | None = None
    channel: ChannelRef | None = None
    quality: MediaQualityBrief | None = None
    segment_count: int = 0


class MediaQualityOut(ORM):
    media_id: uuid.UUID
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    bitrate: int | None = None
    container: str | None = None
    real_bandwidth_hz: float | None = None
    upsampled_suspect: bool | None = None
    lufs_integrated: float | None = None
    true_peak_db: float | None = None
    clipping_ratio: float | None = None
    speech_sec: float | None = None
    speech_ratio: float | None = None
    vad_agreement: float | None = None
    snr_mean: float | None = None
    snr_p10: float | None = None
    c50_mean: float | None = None
    dnsmos_sig: float | None = None
    dnsmos_bak: float | None = None
    dnsmos_ovrl: float | None = None
    dnsmos_p808: float | None = None
    details: dict = {}
    computed_at: datetime | None = None


class TrackOut(BaseModel):
    id: uuid.UUID
    language: str
    kind: str
    format: str | None = None
    s2t_converted: bool = False        # cues converted Simplified → Traditional
    script: str | None = None          # simplified | traditional | neutral | mixed | unknown | not_chinese | None (never examined)
    cues_converted: int | None = None


class MediaDetailOut(BaseModel):
    """MediaSummary + detail fields (quality here is the full MediaQuality)."""
    id: uuid.UUID
    platform: str
    platform_media_id: str
    url: str
    kind: str
    title: str
    thumbnail_url: str | None = None
    duration_sec: float | None = None
    published_at: datetime | None = None
    view_count: int | None = None
    license: str | None = None
    has_manual_captions: bool | None = None
    has_auto_captions: bool | None = None
    manual_caption_langs: list[str] = []
    auto_caption_langs: list[str] = []
    status: str
    stage: str | None = None
    failure_code: str | None = None
    detailed_at: datetime | None = None
    channel: ChannelRef | None = None
    segment_count: int = 0
    description: str = ""
    tags: list = []
    categories: list = []
    chapters: list = []
    language: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    availability: str | None = None
    age_limit: int | None = None
    metadata: dict = {}
    failure_message: str | None = None
    jobs: list[JobOut] = []
    tracks: list[TrackOut] = []
    playlists: list[uuid.UUID] = []
    quality: MediaQualityOut | None = None
    text_converted: bool = False       # some track's cues were converted Simplified → Traditional
    text_convertible: bool = False     # POST /media/{id}/convert-text could still change something
    text_conversion: dict | None = None  # metadata["text_conversion"] summary (counts, config, opencc version)


class MediaFacets(BaseModel):
    kind: dict[str, int]
    captions: dict[str, int]
    license: dict[str, int]
    status: dict[str, int]


class MediaList(BaseModel):
    items: list[MediaSummary]
    total: int
    facets: MediaFacets


class ReprocessIn(BaseModel):
    from_step: str


# ───────────────────────────── segments ─────────────────────────────

class SpeakerRef(BaseModel):
    id: uuid.UUID
    label: str
    display_name: str | None = None


class ProfileBrief(BaseModel):
    age: float | None = None
    sex: str | None = None
    emotion_label: str | None = None


class SegmentSummary(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    media_title: str = ""
    channel_id: uuid.UUID | None = None
    channel_title: str | None = None
    start_sec: float
    end_sec: float
    duration: float
    text: str
    original_text: str
    corrected_text: str | None = None
    text_source: str
    language: str | None = None
    local_speaker: str | None = None
    speaker: SpeakerRef | None = None
    speaker_override: str | None = None
    speaker_state: str
    speech_ratio: float | None = None
    snr: float | None = None
    c50: float | None = None
    dnsmos_ovrl: float | None = None
    dnsmos_p808: float | None = None
    repeated_overlap: float = 0.0
    flags: list = []
    review_status: str
    revision: int
    profile: ProfileBrief | None = None


class VoiceProfileOut(ORM):
    id: uuid.UUID
    revision: int
    backbone: str
    age: float | None = None
    age_group: str | None = None
    sex: str | None = None
    sex_prob: float | None = None
    voice_quality: dict = {}
    emotion: dict = {}
    emotion_dim: dict = {}
    speech_flow: dict = {}
    accent_broad: dict = {}
    accent_narrow: dict = {}
    model_versions: dict = {}
    created_at: datetime | None = None


class UserRef(BaseModel):
    id: uuid.UUID
    display_name: str


class AuditOut(BaseModel):
    id: int
    at: datetime
    user: UserRef | None = None
    user_id: uuid.UUID | None = None
    username: str | None = None
    action: str
    entity_type: str
    entity_id: str
    before: dict | None = None
    after: dict | None = None
    created_at: datetime


class SegmentDetail(SegmentSummary):
    track_id: uuid.UUID | None = None
    cue_ids: list = []
    dnsmos_sig: float | None = None
    dnsmos_bak: float | None = None
    clipping_ratio: float | None = None
    lufs: float | None = None
    review_note: str = ""
    reviewed_by: UserRef | None = None
    reviewed_at: datetime | None = None
    profile: VoiceProfileOut | None = None  # type: ignore[assignment]  (full profile in detail)
    history: list[AuditOut] = []


ReviewStatus = Literal["unchecked", "passed", "needs_fix", "excluded"]
SpeakerState = Literal["unknown", "single", "multiple", "overlap"]


class SegmentPatchIn(BaseModel):
    corrected_text: str | None = Field(default=None, max_length=10_000)
    review_status: ReviewStatus | None = None
    review_note: str | None = Field(default=None, max_length=10_000)
    speaker_state: SpeakerState | None = None
    start_sec: float | None = Field(default=None, ge=0)
    end_sec: float | None = Field(default=None, ge=0)


class SpeakerOverrideIn(BaseModel):
    speaker_name: str = Field(min_length=1, max_length=200)


class SimilarOut(BaseModel):
    segment: SegmentSummary
    similarity: float


# ───────────────────────────── timeline ─────────────────────────────

class DiarTurn(BaseModel):
    speaker: str
    start: float
    end: float
    overlap: bool


class RepeatedSpan(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    start: float
    end: float
    position: str
    excluded: bool


class CueOut(BaseModel):
    start: float
    end: float
    text: str


class Timeline(BaseModel):
    duration: float | None = None
    hop_sec: float | None = None
    vad: list[float] = []
    snr: list[float] = []
    speech_regions: list[list[float]] = []
    diarization: list[DiarTurn] = []
    repeated: list[RepeatedSpan] = []
    cues: list[CueOut] = []
    track_id: uuid.UUID | None = None
    segments: list[SegmentSummary] = []


# ───────────────────────────── speakers ─────────────────────────────

class ClusterIn(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)
    scope: dict = {}


class ClusterRunOut(ORM):
    id: uuid.UUID
    model_id: str
    algorithm: str
    threshold: float
    scope: dict = {}
    params: dict = {}
    stats: dict = {}
    is_current: bool
    created_at: datetime


class SpeakerOut(ORM):
    id: uuid.UUID
    run_id: uuid.UUID
    label: str
    display_name: str | None = None
    verified: bool
    segment_count: int
    total_sec: float
    media_count: int
    channel_ids: list = []
    profile: dict = {}


class SpeakerChannel(BaseModel):
    id: uuid.UUID | None
    title: str
    total_sec: float


class SpeakerDetail(SpeakerOut):
    segments: list[SegmentSummary] = []
    channels: list[SpeakerChannel] = []


class SpeakerPatchIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    verified: bool | None = None


class DiversityChannel(BaseModel):
    channel_id: uuid.UUID | None
    title: str
    speakers: int
    total_sec: float
    top_speaker_share: float


class SpeakerHours(BaseModel):
    speaker_id: uuid.UUID
    label: str
    display_name: str | None = None
    total_sec: float


class DiversityOut(BaseModel):
    run_id: uuid.UUID | None = None
    by_sex: dict[str, float]
    by_age_group: dict[str, float]
    by_emotion: dict[str, float]
    by_channel: list[DiversityChannel]
    speaker_hours: list[SpeakerHours]
    gini: float


# ───────────────────────────── quality / repeated ─────────────────────────────

class HistBin(BaseModel):
    bin: float
    count: int


class QualitySummary(BaseModel):
    media_count: int
    histograms: dict[str, list[HistBin]]
    sample_rates: dict[str, int]
    codecs: dict[str, int]
    upsampled_suspect: int


class RepeatedExample(BaseModel):
    media_id: uuid.UUID
    media_title: str = ""
    start: float
    end: float


class RepeatedGroup(BaseModel):
    group_id: uuid.UUID
    position: str
    duration: float
    match_count: int
    region_count: int
    excluded: bool
    examples: list[RepeatedExample]


class RepeatedPatchIn(BaseModel):
    excluded: bool


class RepeatedPatchOut(RepeatedGroup):
    """The updated group (same shape as GET /channels/{id}/repeated items) + what changed."""
    regions: int
    segments_updated: int


# ───────────────────────────── datasets ─────────────────────────────

class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    filters: dict = {}


class DatasetPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    filters: dict | None = None


class ExportFile(BaseModel):
    name: str
    size_bytes: int
    url: str


class PreviewIn(BaseModel):
    filters: dict = {}


class PreviewChannel(BaseModel):
    channel_id: uuid.UUID | None
    title: str
    segment_count: int
    total_sec: float


class PreviewOut(BaseModel):
    segment_count: int
    total_sec: float
    speakers: int
    channels: int
    by_channel: list[PreviewChannel]
    by_sex: dict[str, float]
    excluded: dict[str, int]
    filters: dict


class ExportIn(BaseModel):
    format: Literal["jsonl", "csv"] = "jsonl"
    include_audio: bool = True


class DatasetExportOut(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    status: str
    format: Literal["jsonl", "csv"] = "jsonl"
    include_audio: bool = True
    filters: dict
    segment_count: int
    total_sec: float
    stats: dict
    progress: float = 0.0
    size_bytes: int | None = None
    files: list[str] = []                   # downloadable names (GET /exports/{id}/download/{name})
    file_details: list[ExportFile] = []     # same files with size and download url
    job: JobOut | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None


class DatasetOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    filters: dict
    created_by: UserRef | None = None
    created_at: datetime
    updated_at: datetime
    export_count: int = 0
    segment_count: int | None = None        # from the latest completed export (None: never exported)
    total_sec: float | None = None
    last_export: DatasetExportOut | None = None


# ───────────────────────────── sources ─────────────────────────────

class ResolveIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class ResolveOut(BaseModel):
    type: Literal["video", "channel", "playlist"]
    channel: ChannelSummary | None = None
    playlist: PlaylistOut | None = None
    media: MediaSummary | None = None
    job: JobOut


# ───────────────────────────── stats ─────────────────────────────

class WorkerJob(BaseModel):
    id: uuid.UUID
    kind: str
    media_title: str | None = None


class WorkerOut(BaseModel):
    name: str
    queues: list[str]
    alive: bool
    last_seen_at: datetime | None = None
    current_job: WorkerJob | None = None


class StatsOverview(BaseModel):
    channels: int
    media: dict[str, int]
    hours: dict[str, float]
    segments: dict[str, int]
    speakers: dict[str, int]
    storage_bytes: int
    workers: list[WorkerOut]
    queue_depth: dict[str, int]


class Ok(BaseModel):
    ok: bool = True


AnyDict = dict[str, Any]
