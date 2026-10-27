import { t } from '@/i18n'
import type * as T from './types'

export const API_BASE = '/api'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, detail: string, code: string) {
    super(detail)
    this.status = status
    this.code = code
  }
}

type Query = Record<string, unknown>

/** Serialises arrays as comma lists (contract), drops empty values. */
export function qs(params?: Query): string {
  if (!params) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(','))
    } else sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn
}

async function request<R>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<R> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res: Response
  try {
    res = await fetch(API_BASE + path, { method, headers, body: payload, credentials: 'same-origin', ...init })
  } catch {
    throw new ApiError(0, t('errors.network'), 'network')
  }
  if (res.status === 204) return undefined as R
  const isJson = res.headers.get('content-type')?.includes('application/json')
  const data = isJson ? await res.json().catch(() => null) : null
  if (!res.ok) {
    const b = (data ?? {}) as Partial<T.ApiErrorBody>
    const detail = typeof b.detail === 'string' ? b.detail : t('errors.server', res.status)
    if (res.status === 401 && !path.startsWith('/auth/login')) onUnauthorized?.()
    throw new ApiError(res.status, detail, b.code ?? `http_${res.status}`)
  }
  return data as R
}

const get = <R>(p: string, q?: Query) => request<R>('GET', p + qs(q))
const post = <R>(p: string, b?: unknown) => request<R>('POST', p, b ?? {})
const put = <R>(p: string, b?: unknown) => request<R>('PUT', p, b)
const patch = <R>(p: string, b?: unknown) => request<R>('PATCH', p, b)
const del = <R>(p: string) => request<R>('DELETE', p)

export const api = {
  // auth
  login: (username: string, password: string) => post<T.User>('/auth/login', { username, password }),
  logout: () => post<void>('/auth/logout'),
  me: () => get<T.User>('/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    post<void>('/auth/password', { current_password, new_password }),
  // users
  users: () => get<T.Page<T.User> | T.User[]>('/users').then((r) => (Array.isArray(r) ? r : r.items)),
  createUser: (b: { username: string; display_name: string; password: string; role: T.Role }) => post<T.User>('/users', b),
  updateUser: (id: string, b: Partial<{ display_name: string; role: T.Role; is_active: boolean; password: string }>) =>
    patch<T.User>(`/users/${id}`, b),
  // settings
  settings: () => get<T.Settings>('/settings'),
  putSetting: <K extends 'youtube' | 'pipeline' | 'quality_thresholds'>(key: K, value: T.Settings[K]) =>
    put<T.Settings[K]>(`/settings/${key}`, value),
  uploadCookies: (file: File) => {
    const fd = new FormData()
    fd.set('file', file)
    return request<T.CookiesState>('PUT', '/settings/cookies', fd)
  },
  deleteCookies: () => del<void>('/settings/cookies'),
  // sources
  resolve: (url: string) => post<T.ResolveResult>('/sources/resolve', { url }),
  // channels
  channels: (q?: string) => get<T.Page<T.ChannelSummary>>('/channels', { q, limit: 500 }),
  channel: (id: string) => get<T.ChannelDetail>(`/channels/${id}`),
  refreshChannel: (id: string) => post<T.Job>(`/channels/${id}/refresh`),
  detailChannel: (id: string, media_ids?: string[]) => post<{ queued: number }>(`/channels/${id}/detail`, { media_ids }),
  patchChannel: (id: string, notes: string) => patch<T.ChannelDetail>(`/channels/${id}`, { notes }),
  channelDeleteImpact: (id: string) => get<T.ChannelDeleteImpact>(`/channels/${id}/delete-impact`),
  deleteChannel: (id: string, confirm?: string) =>
    request<T.ChannelDeleteImpact & { objects_deleted: number }>('DELETE', `/channels/${id}` + qs({ confirm })),
  playlists: (channel_id: string) => get<T.Page<T.Playlist>>('/playlists', { channel_id }), // ext
  playlist: (id: string) => get<T.Playlist>(`/playlists/${id}`),
  repeated: (channel_id: string) => get<T.RepeatedGroup[]>(`/channels/${channel_id}/repeated`),
  patchRepeated: (group_id: string, excluded: boolean) => patch<T.RepeatedGroup>(`/repeated/${group_id}`, { excluded }),
  dedup: (channel_id: string) => post<T.Job>(`/channels/${channel_id}/dedup`),
  // media
  media: (q: T.MediaQuery) => get<T.MediaList>('/media', q as Query),
  mediaDetail: (id: string) => get<T.MediaDetail>(`/media/${id}`),
  download: (b: { media_ids?: string[]; filter?: T.MediaQuery; caption_langs?: string[]; include_auto: boolean }) =>
    post<{ queued: number; skipped: number }>('/media/download', b),
  retryMedia: (id: string) => post<T.Job>(`/media/${id}/retry`),
  reprocess: (id: string, from_step: string) => post<T.Job>(`/media/${id}/reprocess`, { from_step }),
  timeline: (id: string) => get<T.Timeline>(`/media/${id}/timeline`),
  mediaQuality: (id: string) => get<T.MediaQuality>(`/media/${id}/quality`),
  // segments
  segments: (q: T.SegmentQuery) => get<T.Page<T.SegmentSummary>>('/segments', q as Query),
  segment: (id: string) => get<T.SegmentDetail>(`/segments/${id}`),
  patchSegment: (id: string, b: T.SegmentPatch) => patch<T.SegmentDetail>(`/segments/${id}`, b),
  setSegmentSpeaker: (id: string, speaker_name: string) => put<T.SegmentDetail>(`/segments/${id}/speaker`, { speaker_name }),
  clearSegmentSpeaker: (id: string) => del<void>(`/segments/${id}/speaker`),
  similar: (id: string, k = 8) => get<{ segment: T.SegmentSummary; similarity: number }[]>(`/segments/${id}/similar`, { k }),
  // speakers
  cluster: (threshold: number, channel_ids?: string[]) =>
    post<T.Job>('/speakers/cluster', { threshold, scope: channel_ids?.length ? { channel_ids } : {} }),
  clusterRuns: () => get<T.ClusterRun[] | T.Page<T.ClusterRun>>('/speakers/runs').then((r) => (Array.isArray(r) ? r : r.items)),
  speakers: (q: { run_id?: string; channel_id?: string; sort?: string; q?: string; limit?: number; offset?: number }) =>
    get<T.Page<T.Speaker>>('/speakers', q),
  speaker: (id: string) => get<T.SpeakerDetail>(`/speakers/${id}`),
  patchSpeaker: (id: string, b: { display_name?: string | null; verified?: boolean }) => patch<T.Speaker>(`/speakers/${id}`, b),
  diversity: (channel_id?: string) => get<T.Diversity>('/speakers/diversity', { channel_id }),
  // quality
  qualitySummary: (channel_id?: string) => get<T.QualitySummary>('/quality/summary', { channel_id }),
  // datasets
  datasets: () => get<T.Dataset[] | T.Page<T.Dataset>>('/datasets').then((r) => (Array.isArray(r) ? r : r.items)),
  dataset: (id: string) => get<T.Dataset>(`/datasets/${id}`),
  createDataset: (b: { name: string; description: string; filters: T.DatasetFilters }) => post<T.Dataset>('/datasets', b),
  patchDataset: (id: string, b: Partial<{ name: string; description: string; filters: T.DatasetFilters }>) =>
    patch<T.Dataset>(`/datasets/${id}`, b),
  deleteDataset: (id: string) => del<void>(`/datasets/${id}`),
  previewDataset: (filters: T.DatasetFilters) => post<T.DatasetPreview>('/datasets/preview', { filters }),
  exportDataset: (id: string, format: 'jsonl' | 'csv', include_audio: boolean) =>
    post<T.DatasetExport>(`/datasets/${id}/export`, { format, include_audio }),
  exports: (dataset_id: string) =>
    get<T.DatasetExport[] | T.Page<T.DatasetExport>>(`/datasets/${dataset_id}/exports`).then((r) => (Array.isArray(r) ? r : r.items)),
  // jobs & stats
  jobs: (q: { status?: string[]; kind?: string[]; media_id?: string; limit?: number; offset?: number }) =>
    get<T.JobList>('/jobs', q),
  retryJob: (id: string) => post<T.Job>(`/jobs/${id}/retry`),
  cancelJob: (id: string) => post<T.Job>(`/jobs/${id}/cancel`),
  overview: () => get<T.Overview>('/stats/overview'),
}

export const audioUrl = {
  segment: (id: string, pad = 0.3, rev?: number) => `${API_BASE}/audio/segments/${id}${qs({ pad, rev })}`,
  media: (id: string) => `${API_BASE}/audio/media/${id}`,
  exportFile: (exportId: string, name: string) => `${API_BASE}/exports/${exportId}/download/${name}`,
}
