/* Types for the VOTI HTTP API — mirrors docs/ARCHITECTURE.md "HTTP API".
   Fields marked `// ext` are assumed extensions not (yet) in the contract; the UI degrades when absent. */

export type UUID = string
export type ISODate = string

export interface Page<T> {
  items: T[]
  total: number
}

export interface ApiErrorBody {
  detail: string
  code: string
}

// ─────────────── auth & users ───────────────
export type Role = 'admin' | 'annotator'
export interface User {
  id: UUID
  username: string
  display_name: string
  role: Role
  is_active: boolean
  created_at: ISODate
  last_login_at: ISODate | null
}

// ─────────────── settings ───────────────
export interface YoutubeSettings {
  rate_limit_kbps: number
  sleep_between_downloads_sec: number
  sleep_between_requests_sec?: number
  max_concurrent_downloads: number
  cookies_enabled: boolean
  /** scheduled re-listing of every channel, in hours; 0 = off */
  auto_refresh_hours?: number
}
export type VoxBackbone = 'whisper-large-v3' | 'wavlm-large'
export interface PipelineSettings {
  voxprofile_backbone: VoxBackbone
  diarization_enabled: boolean
  segment_min_sec: number
  segment_max_sec: number
  edge_pad_sec: number
}
export interface QualityThresholds {
  low_speech_ratio: number
  low_snr_db: number
  clipping_ratio: number
}
export type OpenccConfig = 's2twp' | 's2tw' | 's2t'
export interface TextSettings {
  convert_simplified: boolean
  opencc_config: OpenccConfig
}
/** caption ↔ audio validation (settings key `validation`) */
export interface ValidationSettings {
  enabled: boolean
  lid_min_prob: number
  lid_min_speech_sec: number
  align_min_score: number
  align_min_units: number
}
export interface CookiesState {
  present: boolean
  updated_at: ISODate | null
}
export interface Settings {
  youtube: YoutubeSettings
  pipeline: PipelineSettings
  quality_thresholds: QualityThresholds
  text?: TextSettings
  validation?: ValidationSettings
  cookies: CookiesState
}

// ─────────────── jobs ───────────────
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'blocked' | 'cancelled' | 'interrupted'
export type QueueName = 'scan' | 'detail' | 'download' | 'audio' | 'ml' | 'export'
export type JobKind =
  | 'source.resolve'
  | 'channel.list'
  | 'playlist.list'
  | 'media.detail'
  | 'media.download'
  | 'media.normalize'
  | 'media.quality'
  | 'media.diarize'
  | 'media.segment'
  | 'media.embed'
  | 'media.profile'
  | 'media.fingerprint'
  | 'channel.dedup'
  | 'speakers.cluster'
  | 'dataset.export'
  | 'media.convert_text'
  | 'media.align'
  | 'media.validate'
  | 'channel.align'

export interface Job {
  id: UUID
  kind: JobKind
  queue: QueueName
  status: JobStatus
  progress: number
  phase: string
  message: string
  error_code: string | null
  error_message: string | null
  attempts: number
  media_id: UUID | null
  channel_id: UUID | null
  media_title?: string | null
  channel_title?: string | null // ext
  created_at: ISODate
  started_at: ISODate | null
  finished_at: ISODate | null
  eta_sec?: number | null
  /** queued for an automatic retry: not started before this time */
  run_after?: ISODate | null
  max_attempts?: number | null // ext
  payload?: Record<string, unknown>
  result?: Record<string, unknown>
}
export interface JobList extends Page<Job> {
  counts: Partial<Record<JobStatus, number>>
}

// ─────────────── channels & playlists ───────────────
export interface ChannelDeleteImpact {
  media_total: number
  playlists: number
  downloaded_media: number
  downloaded_sec: number
  segments: number
  reviewed_segments: number
  storage_bytes: number
  active_jobs: number
  datasets_referencing: number
  /** true → DELETE must carry ?confirm=<exact channel title> */
  requires_confirmation: boolean
}

export type ListStatus = 'new' | 'queued' | 'listing' | 'listed' | 'failed'
/** The channel's current (or last) listing job, trimmed. */
export interface ListingJobBrief {
  id: UUID
  kind?: 'channel.list' | 'source.resolve' | string
  status: JobStatus
  progress: number
  phase: string | null
  message?: string | null
  run_after?: ISODate | null
  error_code?: string | null
}
/** Background caption-detail fetching for the channel's listed media. */
export interface DetailProgress {
  detailed: number
  total: number
  failed?: number
  active: boolean
}
export interface ChannelSummary {
  id: UUID
  platform: string
  title: string
  handle: string | null
  url: string
  thumbnail_url: string | null
  subscriber_count: number | null
  video_count: number | null
  list_status: ListStatus
  last_listed_at: ISODate | null
  /** `new` = videos found by a re-listing in the last 7 days that are still listed/detailed */
  counts: { listed: number; detailed: number; with_manual_captions: number; downloaded: number; ready: number; failed: number; new?: number }
  /** segments = active segments that are not invalid; invalid = invalid segments (hours) */
  hours: { listed: number; ready: number; speech: number; segments?: number; invalid?: number }
  listing_job?: ListingJobBrief | null // ext
  detail_progress?: DetailProgress | null // ext
  /** legacy name of counts.new */
  new_count?: number
  next_refresh_at?: ISODate | null // ext
}
export interface ChannelDetail extends ChannelSummary {
  description: string
  banner_url: string | null
  country: string | null
  tags: string[]
  notes: string
  metadata: Record<string, unknown>
  speakers: { candidates: number; total_sec: number }
  quality: { snr_mean: number | null; dnsmos_ovrl: number | null; speech_ratio?: number | null; [k: string]: number | null | undefined }
  kind_counts?: { video: number; short: number; live?: number } // ext
  playlist_count?: number // ext
}
export interface Playlist {
  id: UUID
  platform: string
  title: string
  description: string
  url: string
  channel_id: UUID | null
  list_status: ListStatus
  last_listed_at: ISODate | null
  media_count: number
  thumbnail_url?: string | null // ext
}

// ─────────────── media ───────────────
export type MediaKind = 'video' | 'short' | 'live' | 'upload'
export type MediaStatus =
  | 'listed'
  | 'detailed'
  | 'queued'
  | 'downloading'
  | 'downloaded'
  | 'processing'
  | 'ready'
  | 'failed'
  | 'blocked'
  | 'skipped'
export type License = 'youtube' | 'creative_commons'

export interface MediaQualityBrief {
  snr_mean: number | null
  dnsmos_ovrl: number | null
  real_bandwidth_hz: number | null
  speech_ratio: number | null
  sample_rate?: number | null // ext
  upsampled_suspect?: boolean | null // ext
}
export interface MediaSummary {
  id: UUID
  platform: string
  platform_media_id: string
  url: string
  kind: MediaKind
  title: string
  thumbnail_url: string | null
  duration_sec: number | null
  published_at: ISODate | null
  view_count: number | null
  license: License | null
  has_manual_captions: boolean | null
  has_auto_captions: boolean | null
  manual_caption_langs: string[]
  auto_caption_langs: string[]
  status: MediaStatus
  stage: string | null
  failure_code: string | null
  channel: { id: UUID; title: string } | null
  quality?: MediaQualityBrief | null
  segment_count: number
  /** found by a re-listing ≤ 7 days ago and still listed/detailed */
  is_new?: boolean
  discovered_at?: ISODate | null
  downloaded?: boolean
  /** active segments whose caption does not match the audio */
  invalid_count?: number
  invalid_sec?: number
}
export interface MediaQuality {
  sample_rate: number | null
  channels: number | null
  codec: string | null
  bitrate: number | null
  container: string | null
  real_bandwidth_hz: number | null
  upsampled_suspect: boolean | null
  lufs_integrated: number | null
  true_peak_db: number | null
  clipping_ratio: number | null
  speech_sec: number | null
  speech_ratio: number | null
  vad_agreement: number | null
  snr_mean: number | null
  snr_p10: number | null
  c50_mean: number | null
  dnsmos_sig: number | null
  dnsmos_bak: number | null
  dnsmos_ovrl: number | null
  dnsmos_p808: number | null
  computed_at?: ISODate
  details?: {
    spectrum?: { hz: number; db: number }[] // ext: long-term average spectrum of the original audio
    model_versions?: Record<string, string>
    [k: string]: unknown
  }
}
export interface Chapter {
  title: string
  start_time: number
  end_time: number
}
export interface MediaDetail extends Omit<MediaSummary, 'quality'> {
  description: string
  tags: string[]
  categories: string[]
  chapters: Chapter[]
  language: string | null
  like_count: number | null
  comment_count: number | null
  availability: string | null
  age_limit: number | null
  metadata: Record<string, unknown>
  failure_message: string | null
  jobs: Job[]
  tracks: Track[]
  quality: MediaQuality | null
  validity_counts?: { valid: number; invalid: number; unknown: number }
  /** a media.validate job is queued/running */
  validation_job_active?: boolean
  /** some track was converted Simplified → Traditional */
  text_converted?: boolean
  /** POST /media/{id}/convert-text would still change something */
  text_convertible?: boolean
  text_conversion?: TextConversion | null
}
export type TrackScript = 'simplified' | 'traditional' | 'neutral' | 'mixed' | 'unknown' | 'not_chinese'
export interface Track {
  id: UUID
  language: string
  kind: 'manual' | 'auto' | 'translated' | 'uploaded'
  format?: string
  s2t_converted?: boolean
  /** null = never examined */
  script?: TrackScript | null
  cues_converted?: number | null
}
export interface TextConversion {
  converted: boolean
  cues_converted?: number
  segments_converted?: number
  config?: string | null
  opencc?: string | null
  at?: ISODate | null
  [k: string]: unknown
}
export interface MediaFacets {
  kind: Partial<Record<MediaKind, number>>
  captions: { manual: number; auto_only: number; none: number; unknown: number }
  license: Partial<Record<License, number>>
  status: Partial<Record<MediaStatus, number>>
  downloaded?: { yes: number; no: number }
  new?: number
}
export interface MediaList extends Page<MediaSummary> {
  facets: MediaFacets
}
export type CaptionFilter = 'manual' | 'auto' | 'any' | 'none'
export type MediaSort = 'published_at' | '-published_at' | 'duration' | '-duration' | 'views' | '-views' | 'title'
export interface MediaQuery {
  channel_id?: UUID
  playlist_id?: UUID
  q?: string
  kind?: MediaKind[]
  status?: MediaStatus[]
  captions?: CaptionFilter
  include_auto?: boolean
  caption_lang?: string
  license?: License
  min_duration?: number
  max_duration?: number
  published_after?: string
  published_before?: string
  detailed?: boolean
  upsampled_suspect?: boolean // ext
  downloaded?: boolean // ext
  sort?: MediaSort
  limit?: number
  offset?: number
}

export interface Timeline {
  duration: number | null
  hop_sec: number | null
  vad: number[]
  snr: number[]
  speech_regions: [number, number][]
  diarization: { speaker: string; start: number; end: number; overlap: boolean }[]
  repeated: { id: UUID; start: number; end: number; position: RepeatPosition; excluded: boolean }[]
  cues: { start: number; end: number; text: string }[]
  segments: SegmentSummary[]
}

// ─────────────── segments ───────────────
export type ReviewStatus = 'unchecked' | 'passed' | 'needs_fix' | 'excluded'
export type SpeakerState = 'unknown' | 'single' | 'multiple' | 'overlap'
export type TextSource = 'manual_caption' | 'auto_caption' | 'human_revision' | 'none' | string
export type Validity = 'valid' | 'invalid' | 'unknown'
export type InvalidReason = 'language_mismatch' | 'text_mismatch' | 'manual'
export interface SegmentSummary {
  id: UUID
  media_id: UUID
  media_title: string
  channel_title: string
  start_sec: number
  end_sec: number
  duration: number
  text: string
  original_text: string
  corrected_text: string | null
  text_source: TextSource
  local_speaker: string | null
  speaker: { id: UUID; label: string; display_name: string | null } | null
  speaker_state: SpeakerState
  speech_ratio: number | null
  snr: number | null
  c50: number | null
  dnsmos_ovrl: number | null
  dnsmos_p808: number | null
  flags: string[]
  review_status: ReviewStatus
  revision: number
  profile?: { age: number | null; sex: string | null; emotion_label: string | null } | null
  validity?: Validity
  invalid_reason?: InvalidReason | null
  validity_source?: 'auto' | 'manual' | null
  /** VoxLingua107 code of the spoken language (e.g. en) */
  lid_lang?: string | null
  lid_prob?: number | null
  align_score?: number | null
}
export type Dist = Record<string, number>
export interface VoiceProfile {
  backbone: VoxBackbone
  age: number | null
  age_group: string | null
  sex: string | null
  sex_prob: number | null
  voice_quality: Dist
  emotion: Dist
  emotion_dim: Dist
  speech_flow: Dist
  accent_broad: Dist
  accent_narrow: Dist
  model_versions: Record<string, string>
}
export interface AuditLog {
  id: UUID
  at: ISODate
  user: { id: UUID; display_name: string } | null
  action: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}
export interface SegmentDetail extends Omit<SegmentSummary, 'profile'> {
  track_id: UUID | null
  cue_ids: UUID[]
  dnsmos_sig: number | null
  dnsmos_bak: number | null
  clipping_ratio: number | null
  lufs: number | null
  repeated_overlap: number
  review_note: string
  reviewed_by: { id: UUID; display_name: string } | null
  reviewed_at: ISODate | null
  profile: VoiceProfile | null
  history: AuditLog[]
  /** character/word timings; units concatenate exactly to `text`; null times = untimed (punctuation) */
  words?: WordUnit[] | null
  alignment?: SegmentAlignment | null
  /** segments.validation: lid top-3, expected language, thresholds … */
  validation?: {
    lid?: [string, number][] | null
    expected?: string | null
    align_units?: number
    thresholds?: Record<string, number>
    at?: ISODate
    note?: string
    [k: string]: unknown
  } | null
}
export type AlignStatus = 'aligned' | 'skipped' | 'failed' | 'pending' | 'stale' | 'none'
export interface SegmentAlignment {
  status: AlignStatus
  /** e.g. no_romanizer:nan, unsupported_language:en, too_long:72s */
  reason: string | null
  model_id?: string | null
  updated_at?: ISODate | null
}
export interface WordUnit {
  t: string
  s: number | null
  e: number | null
}
/** segment id → [[unit, start, end], …] in absolute media seconds */
export type MediaWords = Record<UUID, [string, number | null, number | null][]>
/** GET /media/{id}/words */
export interface MediaWordsResponse {
  words: MediaWords
  segments: number
  status: Partial<Record<AlignStatus, number>>
  job_active: boolean
}
export interface SegmentNeighbors {
  prev_id: UUID | null
  next_id: UUID | null
  /** 0-based position in the filtered list */
  index: number | null
  total: number
  in_filter?: boolean
}
export interface SegmentQuery {
  media_id?: UUID
  channel_id?: UUID
  speaker_id?: UUID
  review_status?: ReviewStatus[]
  text_source?: string[]
  q?: string
  min_snr?: number
  min_dnsmos?: number
  min_speech_ratio?: number
  flags?: string[]
  exclude_flags?: string[]
  has_profile?: boolean
  validity?: Validity[]
  sort?: string
  limit?: number
  offset?: number
}
export interface SegmentPatch {
  corrected_text?: string | null
  review_status?: ReviewStatus
  review_note?: string
  speaker_state?: SpeakerState
  start_sec?: number
  end_sec?: number
  /** human decision; auto = drop it and re-run the automatic check */
  validity?: 'valid' | 'invalid' | 'auto'
}

// ─────────────── speakers ───────────────
export interface SpeakerProfileAgg {
  sex?: Dist
  age_mean?: number | null
  age_group?: Dist
  emotion?: Dist
  voice_quality?: Dist
}
export interface Speaker {
  id: UUID
  run_id: UUID
  label: string
  display_name: string | null
  verified: boolean
  segment_count: number
  total_sec: number
  media_count: number
  channel_ids: UUID[]
  profile: SpeakerProfileAgg
}
export interface SpeakerDetail extends Speaker {
  segments: SegmentSummary[]
  channels: { id: UUID; title: string; total_sec: number }[]
}
export interface ClusterRun {
  id: UUID
  model_id: string
  algorithm: string
  threshold: number
  scope: { channel_ids?: UUID[] }
  params: Record<string, unknown>
  stats: { speakers?: number; segments?: number; singletons?: number; [k: string]: unknown }
  is_current: boolean
  created_at: ISODate
}
export interface Diversity {
  by_sex: Dist
  by_age_group: Dist
  by_emotion: Dist
  by_channel: { channel_id: UUID; title: string; speakers: number; total_sec: number; top_speaker_share: number }[]
  speaker_hours: { speaker_id: UUID; label: string; total_sec: number }[]
  gini: number
}

// ─────────────── quality ───────────────
export interface HistBin {
  bin: number
  count: number
}
export interface QualitySummary {
  histograms: {
    snr: HistBin[]
    dnsmos_ovrl: HistBin[]
    c50: HistBin[]
    bandwidth_hz: HistBin[]
    speech_ratio: HistBin[]
  }
  sample_rates: Record<string, number>
  codecs: Record<string, number>
  upsampled_suspect: number
  media_count?: number // ext
}

// ─────────────── repeated ───────────────
export type RepeatPosition = 'intro' | 'outro' | 'middle'
export interface RepeatedGroup {
  group_id: UUID
  position: RepeatPosition
  duration: number
  match_count: number
  excluded: boolean
  examples: { media_id: UUID; start: number; end: number; media_title?: string /* ext */ }[]
}

// ─────────────── datasets ───────────────
export interface DatasetFilters {
  channel_ids: UUID[]
  media_ids: UUID[]
  speaker_ids: UUID[]
  text_sources: string[]
  review_status: ReviewStatus[]
  exclude_repeated: boolean
  max_repeated_overlap: number
  speaker_states: SpeakerState[]
  min_duration: number | null
  max_duration: number | null
  min_snr: number | null
  min_c50: number | null
  min_dnsmos_ovrl: number | null
  min_dnsmos_p808: number | null
  min_speech_ratio: number | null
  min_bandwidth_hz: number | null
  exclude_upsampled: boolean
  exclude_flags: string[]
  licenses: License[]
  sex: string[]
  age_groups: string[]
  max_sec_per_speaker: number | null
}
export interface DatasetPreview {
  segment_count: number
  total_sec: number
  speakers: number
  channels: number
  by_channel: { channel_id: UUID; title: string; total_sec: number; segment_count?: number }[]
  by_sex: Dist
  excluded: { repeated: number; auto_caption: number; low_quality: number; [k: string]: number }
}
export type ExportStatus = 'queued' | 'running' | 'completed' | 'failed'
export interface DatasetExport {
  id: UUID
  dataset_id: UUID
  status: ExportStatus
  format?: 'jsonl' | 'csv' // ext
  include_audio?: boolean // ext
  segment_count: number
  total_sec: number
  stats: Record<string, unknown>
  progress?: number // ext
  size_bytes?: number | null // ext
  files?: string[] // ext: downloadable names
  created_at: ISODate
  finished_at?: ISODate | null // ext
}
export interface Dataset {
  id: UUID
  name: string
  description: string
  filters: DatasetFilters
  created_at: ISODate
  updated_at: ISODate
  created_by?: { id: UUID; display_name: string } | null // ext
  last_export?: DatasetExport | null // ext
  segment_count?: number // ext: cached preview
  total_sec?: number // ext
}

// ─────────────── overview ───────────────
export interface Overview {
  channels: number
  media: { listed: number; detailed: number; downloaded: number; ready: number; failed: number }
  hours: { listed: number; downloaded: number; speech: number; segments: number; reviewed: number; invalid?: number }
  segments: { total: number; passed: number; needs_fix: number; invalid?: number }
  speakers: { candidates: number; verified: number }
  storage_bytes: number
  workers: { name: string; queues: QueueName[]; alive: boolean; last_seen_at?: ISODate; current_job?: { id: string; kind: string; media_title?: string | null } | null }[]
  queue_depth: Record<QueueName, number>
}

export interface ResolveResult {
  type: 'video' | 'channel' | 'playlist'
  channel?: ChannelSummary
  playlist?: Playlist
  media?: MediaSummary
  job: Job
  /** the video's channel did not exist before this request (ext) */
  channel_created?: boolean
  /** listing job queued for a newly created channel (ext) */
  channel_job?: Job | null
}
