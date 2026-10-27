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
  segments: (q: T.SegmentQuery) => ['segments', q] as const,
  segment: (id: string) => ['segment', id] as const,
  similar: (id: string) => ['similar', id] as const,
  speakers: (q: object) => ['speakers', q] as const,
  speaker: (id: string) => ['speaker', id] as const,
  runs: ['clusterRuns'] as const,
  diversity: (cid?: string) => ['diversity', cid ?? ''] as const,
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
export const useChannels = (q?: string) =>
  useQuery({ queryKey: qk.channels(q), queryFn: () => api.channels(q), placeholderData: keepPreviousData })
export const useChannel = (id: string) =>
  useQuery({
    queryKey: qk.channel(id),
    queryFn: () => api.channel(id),
    refetchInterval: (q) => (q.state.data?.list_status === 'listing' ? 3000 : false),
  })
export const usePlaylists = (cid: string) => useQuery({ queryKey: qk.playlists(cid), queryFn: () => api.playlists(cid) })
export const useRepeated = (cid: string) => useQuery({ queryKey: qk.repeated(cid), queryFn: () => api.repeated(cid) })
export const useMedia = (q: T.MediaQuery, enabled = true) =>
  useQuery({ queryKey: qk.media(q), queryFn: () => api.media(q), placeholderData: keepPreviousData, enabled })
export const useMediaDetail = (id: string) =>
  useQuery({
    queryKey: qk.mediaDetail(id),
    queryFn: () => api.mediaDetail(id),
    refetchInterval: (q) => (['queued', 'downloading', 'processing', 'downloaded'].includes(q.state.data?.status ?? '') ? 4000 : false),
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
export function useSegmentPatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: T.SegmentPatch }) => api.patchSegment(id, patch),
    onSuccess: (d) => {
      qc.setQueryData(qk.segment(d.id), d)
      // update summaries in any cached segment list without refetching (keeps queue order stable)
      qc.setQueriesData<T.Page<T.SegmentSummary>>({ queryKey: ['segments'] }, (old) =>
        old ? { ...old, items: old.items.map((s) => (s.id === d.id ? { ...s, ...d, profile: s.profile } : s)) } : old,
      )
      qc.invalidateQueries({ queryKey: ['timeline', d.media_id] })
    },
    onError: onErr,
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
