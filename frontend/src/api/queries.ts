import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api, ApiError } from './client'
import { getLang, msgs, t } from '@/i18n'
import type * as T from './types'

export const qk = {
  me: ['me'] as const,
  overview: ['overview'] as const,
  channels: (q?: string) => ['channels', q ?? ''] as const,
  channel: (id: string) => ['channel', id] as const,
  playlists: (cid: string) => ['playlists', cid] as const,
  repeated: (cid: string) => ['repeated', cid] as const,
  media: (q: T.MediaQuery) => ['media', q] as const,
  mediaDetail: (id: string) => ['mediaDetail', id] as const,
  timeline: (id: string) => ['timeline', id] as const,
  mediaWords: (id: string) => ['mediaWords', id] as const,
  mediaOcr: (id: string) => ['mediaOcr', id] as const,
  mediaSpeakers: (id: string) => ['mediaSpeakers', id] as const,
  mediaSegments: (id: string) => ['segments', 'media', id] as const,
  neighbors: (id: string, q: T.SegmentQuery) => ['neighbors', id, q] as const,
  segments: (q: T.SegmentQuery) => ['segments', q] as const,
  segment: (id: string) => ['segment', id] as const,
  similar: (id: string) => ['similar', id] as const,
  speakers: (q: object) => ['speakers', q] as const,
  speaker: (id: string) => ['speaker', id] as const,
  runs: ['clusterRuns'] as const,
  diversity: (cid?: string) => ['diversity', cid ?? ''] as const,
  similarity: (q: object) => ['speakerSimilarity', q] as const,
  quality: (cid?: string) => ['quality', cid ?? ''] as const,
  datasets: ['datasets'] as const,
  dataset: (id: string) => ['dataset', id] as const,
  exports: (id: string) => ['exports', id] as const,
  jobs: (q: object) => ['jobs', q] as const,
  users: ['users'] as const,
  settings: ['settings'] as const,
}

/** User-facing error text in the active language. Server `detail` messages are zh-TW, so in English
 * known error codes are mapped client-side, then generic per-status text, then the server text. */
export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === 'network') return t('errors.network')
    if (getLang() === 'zh-TW') return e.message
    const m = msgs().errors
    const byCode = (m.codes as Record<string, string>)[e.code]
    if (byCode) return byCode
    const byStatus = (m.status as Record<number, string>)[e.status]
    if (byStatus) return byStatus
    return e.message
  }
  if (e instanceof Error && e.message) return e.message
  return t('common.actionFailed')
}
const onErr = (e: unknown) => toast.error(errMsg(e))

export const useMe = () =>
  useQuery({
    queryKey: qk.me,
    queryFn: async () => {
      try {
        return await api.me()
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  })

export const useOverview = () => useQuery({ queryKey: qk.overview, queryFn: api.overview, refetchInterval: 10_000 })
/** Listing has not finished yet (first listing or a re-list in progress). */
export const isListing = (c: Pick<T.ChannelSummary, 'list_status'>) => c.list_status === 'new' || c.list_status === 'queued' || c.list_status === 'listing'
export const useChannels = (q?: string) =>
  useQuery({
    queryKey: qk.channels(q),
    queryFn: () => api.channels(q),
    placeholderData: keepPreviousData,
    // keep newly added channels' badges (queued → listing → listed) and caption progress fresh
    refetchInterval: (x) => (x.state.data?.items.some((c) => isListing(c) || c.detail_progress?.active) ? 4000 : false),
  })
export const useChannel = (id: string) =>
  useQuery({
    queryKey: qk.channel(id),
    queryFn: () => api.channel(id),
    enabled: !!id,
    refetchInterval: (q) => {
      const c = q.state.data
      if (!c) return false
      if (isListing(c)) return 2500
      return c.detail_progress?.active ? 6000 : false
    },
  })
export const usePlaylists = (cid: string) => useQuery({ queryKey: qk.playlists(cid), queryFn: () => api.playlists(cid) })
export const useRepeated = (cid: string) => useQuery({ queryKey: qk.repeated(cid), queryFn: () => api.repeated(cid) })
export const useMedia = (q: T.MediaQuery, enabled = true, poll: number | false = false) =>
  useQuery({ queryKey: qk.media(q), queryFn: () => api.media(q), placeholderData: keepPreviousData, enabled, refetchInterval: poll })
export const useMediaDetail = (id: string) =>
  useQuery({
    queryKey: qk.mediaDetail(id),
    queryFn: () => api.mediaDetail(id),
    refetchInterval: (q) => {
      const m = q.state.data
      if (!m) return false
      if (['queued', 'downloading', 'processing', 'downloaded'].includes(m.status)) return 4000
      // side jobs (caption conversion, word alignment) don't change Media.status
      return m.jobs.some((j) => j.status === 'running' || j.status === 'queued') ? 4000 : false
    },
  })
/** Every segment of a media in time order (transcript). Falls back to the paginated list on older APIs.
 * The key starts with 'segments' so optimistic segment patches update it too. */
export const useMediaSegments = (id: string) =>
  useQuery({
    queryKey: qk.mediaSegments(id),
    queryFn: async () => {
      try {
        return await api.mediaSegments(id)
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return api.segments({ media_id: id, limit: 500 })
        throw e
      }
    },
    enabled: !!id,
  })
/** Word timings for every segment of a media; an empty map (or an error, e.g. an older API) = not aligned. */
export const useMediaWords = (id: string, enabled = true) =>
  useQuery({
    queryKey: qk.mediaWords(id),
    queryFn: () => api.mediaWords(id),
    enabled: enabled && !!id,
    retry: false,
    staleTime: 60_000,
    refetchInterval: (q) => (q.state.data?.job_active || q.state.data?.status.pending ? 5000 : false),
  })
/** Hard-subtitle OCR result of a media (events + crops). Polls while an OCR job is queued/running. */
export const useMediaOcr = (id: string | undefined, enabled = true) =>
  useQuery({
    queryKey: qk.mediaOcr(id ?? ''),
    queryFn: () => api.mediaOcr(id!),
    enabled: enabled && !!id,
    staleTime: 60_000,
    refetchInterval: (q) => (q.state.data?.job_active || q.state.data?.status === 'pending' ? 5000 : false),
  })
const OCR_ERRORS = ['not_downloaded', 'ocr_exists', 'job_active', 'ocr_disabled', 'unsupported_platform'] as const
/** OCR-specific error text (both languages) for POST /media/{id}/ocr, else the generic mapping. */
export function ocrErrMsg(e: unknown): string {
  if (e instanceof ApiError && (OCR_ERRORS as readonly string[]).includes(e.code)) return t(`ocr.errors.${e.code as (typeof OCR_ERRORS)[number]}`)
  return errMsg(e)
}
/** POST /media/{id}/ocr — run (force: re-run) hard-subtitle OCR. */
export function useRunOcr(mediaId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (force: boolean) => api.runOcr(mediaId, force),
    onSuccess: (_j, force) => {
      toast.success(force ? t('ocr.rerunQueued') : t('ocr.runQueued'), { description: t('ocr.queuedHint') })
      qc.invalidateQueries({ queryKey: qk.mediaDetail(mediaId) })
      qc.invalidateQueries({ queryKey: qk.mediaOcr(mediaId) })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (e) => toast.error(ocrErrMsg(e)),
  })
}
export const useNeighbors = (id: string | undefined, q: T.SegmentQuery) =>
  useQuery({ queryKey: qk.neighbors(id ?? '', q), queryFn: () => api.neighbors(id!, q), enabled: !!id, retry: false, placeholderData: keepPreviousData })
/** Diarized speakers of a media (cards + colour order). An older API without the endpoint yields []. */
export const useMediaSpeakers = (id: string, enabled = true) =>
  useQuery({
    queryKey: qk.mediaSpeakers(id),
    queryFn: async () => {
      try {
        return await api.mediaSpeakers(id)
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return [] as T.MediaSpeaker[]
        throw e
      }
    },
    enabled: enabled && !!id,
    staleTime: 60_000,
  })
export const useTimeline = (id: string, enabled = true) => useQuery({ queryKey: qk.timeline(id), queryFn: () => api.timeline(id), enabled })
export const useSegments = (q: T.SegmentQuery, enabled = true) =>
  useQuery({ queryKey: qk.segments(q), queryFn: () => api.segments(q), placeholderData: keepPreviousData, enabled })
export const useSegment = (id: string | undefined) =>
  useQuery({ queryKey: qk.segment(id ?? ''), queryFn: () => api.segment(id!), enabled: !!id })
export const useSimilar = (id: string | undefined) =>
  useQuery({ queryKey: qk.similar(id ?? ''), queryFn: () => api.similar(id!, 6), enabled: !!id })
export const useSpeakers = (q: { run_id?: string; channel_id?: string; sort?: string; q?: string; limit?: number; offset?: number }) =>
  useQuery({ queryKey: qk.speakers(q), queryFn: () => api.speakers(q), placeholderData: keepPreviousData })
export const useSpeaker = (id: string | undefined) =>
  useQuery({ queryKey: qk.speaker(id ?? ''), queryFn: () => api.speaker(id!), enabled: !!id })
export const useClusterRuns = () => useQuery({ queryKey: qk.runs, queryFn: api.clusterRuns })
export const useSpeakerSimilarity = (q: { channel_id?: string; limit?: number; matrix_n?: number }) =>
  useQuery({ queryKey: qk.similarity(q), queryFn: () => api.speakerSimilarity(q), placeholderData: keepPreviousData })
export const useDiversity = (cid?: string) =>
  useQuery({ queryKey: qk.diversity(cid), queryFn: () => api.diversity(cid), placeholderData: keepPreviousData })
export const useQualitySummary = (cid?: string) =>
  useQuery({ queryKey: qk.quality(cid), queryFn: () => api.qualitySummary(cid), placeholderData: keepPreviousData })
export const useDatasets = () => useQuery({ queryKey: qk.datasets, queryFn: api.datasets })
export const useDataset = (id: string | undefined) =>
  useQuery({ queryKey: qk.dataset(id ?? ''), queryFn: () => api.dataset(id!), enabled: !!id })
export const useExports = (id: string | undefined) =>
  useQuery({
    queryKey: qk.exports(id ?? ''),
    queryFn: () => api.exports(id!),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === 'queued' || e.status === 'running') ? 2500 : false),
  })
export const useJobs = (q: { status?: string[]; kind?: string[]; media_id?: string; limit?: number; offset?: number }, poll = 4000) =>
  useQuery({ queryKey: qk.jobs(q), queryFn: () => api.jobs(q), refetchInterval: poll, placeholderData: keepPreviousData })
export const useUsers = () => useQuery({ queryKey: qk.users, queryFn: api.users })
export const useSettings = () => useQuery({ queryKey: qk.settings, queryFn: api.settings })

// ─────────────── mutations ───────────────
type SegPage = T.Page<T.SegmentSummary>
/** Apply a PATCH body to a cached segment the way the server will (text = corrected ?? original). */
function applyPatch<S extends Pick<T.SegmentSummary, 'review_status' | 'corrected_text' | 'text' | 'original_text' | 'speaker_state' | 'validity' | 'invalid_reason' | 'validity_source'>>(s: S, p: T.SegmentPatch): S {
  const n = { ...s, ...(p.review_status ? { review_status: p.review_status } : {}) }
  if (p.validity === 'valid' || p.validity === 'invalid') {
    n.validity = p.validity
    n.validity_source = 'manual'
    n.invalid_reason = p.validity === 'invalid' ? 'manual' : null
  } else if (p.validity === 'auto') n.validity_source = 'auto'
  if (p.corrected_text !== undefined) {
    n.corrected_text = p.corrected_text
    n.text = p.corrected_text ?? s.original_text
  }
  if (p.speaker_state) n.speaker_state = p.speaker_state
  return n
}

/** PATCH /segments/{id} with an optimistic update of every cached copy (detail + lists); rolled back on error. */
export function useSegmentPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: T.SegmentPatch }) => api.patchSegment(id, patch),
    onMutate: async ({ id, patch }) => {
      await qc.cancelQueries({ queryKey: qk.segment(id) })
      const prevDetail = qc.getQueryData<T.SegmentDetail>(qk.segment(id))
      const prevLists = qc.getQueriesData<SegPage>({ queryKey: ['segments'] })
      if (prevDetail) qc.setQueryData(qk.segment(id), applyPatch(prevDetail, patch))
      qc.setQueriesData<SegPage>({ queryKey: ['segments'] }, (old) =>
        old ? { ...old, items: old.items.map((s) => (s.id === id ? applyPatch(s, patch) : s)) } : old,
      )
      return { prevDetail, prevLists }
    },
    onError: (e, { id }, ctx) => {
      if (ctx?.prevDetail) qc.setQueryData(qk.segment(id), ctx.prevDetail)
      ctx?.prevLists.forEach(([k, v]) => qc.setQueryData(k, v))
      onErr(e)
    },
    onSuccess: (d, v) => {
      qc.setQueryData(qk.segment(d.id), d)
      // update summaries in any cached segment list without refetching (keeps queue order stable)
      qc.setQueriesData<T.Page<T.SegmentSummary>>({ queryKey: ['segments'] }, (old) =>
        old ? { ...old, items: old.items.map((s) => (s.id === d.id ? { ...s, ...d, profile: s.profile } : s)) } : old,
      )
      qc.invalidateQueries({ queryKey: ['timeline', d.media_id] })
      if (v.patch.corrected_text !== undefined) qc.invalidateQueries({ queryKey: qk.mediaWords(d.media_id) })
      if (v.patch.validity !== undefined) {
        qc.invalidateQueries({ queryKey: qk.mediaDetail(d.media_id) })
        qc.invalidateQueries({ queryKey: qk.mediaSpeakers(d.media_id) })
      }
    },
  })
}

export function useJobAction() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'retry' | 'cancel' }) =>
      action === 'retry' ? api.retryJob(id) : api.cancelJob(id),
    onSuccess: (_d, v) => {
      toast.success(v.action === 'retry' ? t('common.requeued') : t('common.cancelRequested'))
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: onErr,
  })
}

export { onErr }
