import { delay, http, HttpResponse } from 'msw'
import type * as T from '@/api/types'
import { buildDB, genSegments, newJob, rngFor, summary, tick, topKey, PIPE, type DB, type MockMedia } from './data'
import { synthWav } from './audio'

let db: DB
const SESSION_KEY = 'voti-mock-logged-out'
function getDB() {
  if (!db) {
    db = buildDB()
    try {
      db.me = localStorage.getItem(SESSION_KEY) ? null : db.users[0].id
    } catch {
      db.me = db.users[0].id
    }
  }
  return db
}

const A = '/api'
const lat = () => delay(120 + Math.random() * 180)
const err = (status: number, detail: string, code: string) => HttpResponse.json({ detail, code }, { status })
const json = <X,>(x: X, init?: ResponseInit) => HttpResponse.json(x as never, init)
const user = () => getDB().users.find((u) => u.id === getDB().me) ?? null
const pub = (u: DB['users'][number]): T.User => {
  const { password: _p, ...rest } = u
  void _p
  return rest
}
function page<X>(items: X[], url: URL) {
  const limit = Math.min(500, Number(url.searchParams.get('limit') ?? 50))
  const offset = Number(url.searchParams.get('offset') ?? 0)
  return { items: items.slice(offset, offset + limit), total: items.length }
}
const list = (url: URL, k: string) => url.searchParams.get(k)?.split(',').filter(Boolean) ?? []
const num = (url: URL, k: string) => {
  const v = url.searchParams.get(k)
  return v === null || v === '' ? null : Number(v)
}
const bool = (url: URL, k: string) => {
  const v = url.searchParams.get(k)
  return v === null ? null : v === 'true' || v === '1'
}

// ───────────── media filtering ─────────────
type MF = { [k: string]: unknown }
function mediaFilterFromURL(url: URL): MF {
  const f: MF = {}
  url.searchParams.forEach((v, k) => (f[k] = v))
  return f
}
function matchMedia(m: MockMedia, f: MF, omit?: string) {
  const s = (k: string) => (f[k] === undefined || f[k] === null || f[k] === '' ? null : String(f[k]))
  const arr = (k: string) => {
    const v = f[k]
    return Array.isArray(v) ? (v as string[]) : typeof v === 'string' && v ? v.split(',') : []
  }
  if (s('channel_id') && m.channel?.id !== s('channel_id')) return false
  if (s('playlist_id') && !(getDB().playlistItems.get(s('playlist_id')!) ?? []).includes(m.id)) return false
  if (s('q')) {
    const q = s('q')!.toLowerCase()
    if (!m.title.toLowerCase().includes(q) && !m.description.toLowerCase().includes(q)) return false
  }
  if (omit !== 'kind' && arr('kind').length && !arr('kind').includes(m.kind)) return false
  if (omit !== 'status' && arr('status').length && !arr('status').includes(m.status)) return false
  if (omit !== 'license' && s('license') && m.license !== s('license')) return false
  const includeAuto = f.include_auto === true || f.include_auto === 'true'
  const autoOnly = m.has_manual_captions === false && m.has_auto_captions === true
  if (omit !== 'captions') {
    const c = s('captions')
    if (c === 'manual' && !m.has_manual_captions) return false
    if (c === 'auto' && !m.has_auto_captions) return false
    if (c === 'none' && (m.has_manual_captions !== false || m.has_auto_captions !== false)) return false
    if (c === 'any' && !(m.has_manual_captions || (includeAuto && m.has_auto_captions))) return false
    if (c !== 'auto' && !includeAuto && autoOnly) return false
  }
  if (s('caption_lang') && !m.manual_caption_langs.includes(s('caption_lang')!)) return false
  const d = m.duration_sec ?? 0
  if (s('min_duration') && d < Number(s('min_duration'))) return false
  if (s('max_duration') && d > Number(s('max_duration'))) return false
  if (s('published_after') && (m.published_at ?? '') < s('published_after')!) return false
  if (s('published_before') && (m.published_at ?? '') > s('published_before')! + 'T23:59:59Z') return false
  if (s('detailed') === 'true' && m.has_manual_captions === null) return false
  if (s('detailed') === 'false' && m.has_manual_captions !== null) return false
  if (s('upsampled_suspect') === 'true' && !m.q?.upsampled_suspect) return false
  return true
}
function sortMedia(xs: MockMedia[], sort: string | null) {
  const key = (sort ?? '-published_at').replace('-', '')
  const dir = sort?.startsWith('-') || !sort ? -1 : 1
  const get = (m: MockMedia): number | string =>
    key === 'duration' ? (m.duration_sec ?? 0) : key === 'views' ? (m.view_count ?? 0) : key === 'title' ? m.title : (m.published_at ?? '')
  return [...xs].sort((a, b) => (get(a) < get(b) ? -dir : get(a) > get(b) ? dir : 0))
}
function facets(f: MF): T.MediaFacets {
  const all = getDB().media
  const fc = (omit: string) => all.filter((m) => matchMedia(m, f, omit))
  const k = fc('kind')
  const c = fc('captions')
  const l = fc('license')
  const st = fc('status')
  const count = <X,>(xs: X[], fn: (x: X) => string) => xs.reduce<Record<string, number>>((a, x) => ((a[fn(x)] = (a[fn(x)] ?? 0) + 1), a), {})
  return {
    kind: count(k, (m) => m.kind),
    captions: {
      manual: c.filter((m) => m.has_manual_captions).length,
      auto_only: c.filter((m) => m.has_manual_captions === false && m.has_auto_captions).length,
      none: c.filter((m) => m.has_manual_captions === false && m.has_auto_captions === false).length,
      unknown: c.filter((m) => m.has_manual_captions === null).length,
    },
    license: count(l, (m) => m.license ?? 'youtube'),
    status: count(st, (m) => m.status),
  }
}
const mediaSummary = (m: MockMedia): T.MediaSummary => {
  const { channel_idx: _c, description: _d, sample_rate: _s, q: _q, speakers: _sp, like_count: _l, ...rest } = m
  void _c, _d, _s, _q, _sp, _l
  return rest
}

// ───────────── segment filtering ─────────────
function allSegments() {
  const out: T.SegmentDetail[] = []
  for (const segs of getDB().segments.values()) out.push(...segs)
  return out
}
function filterSegments(url: URL) {
  const d = getDB()
  let xs: T.SegmentDetail[]
  const mid = url.searchParams.get('media_id')
  if (mid) {
    const m = d.mediaById.get(mid)
    xs = m && (m.status === 'ready' || d.segments.has(mid)) ? genSegments(d, m) : []
  } else xs = allSegments()
  const cid = url.searchParams.get('channel_id')
  if (cid) {
    const ids = new Set(d.media.filter((m) => m.channel?.id === cid).map((m) => m.id))
    xs = xs.filter((s) => ids.has(s.media_id))
  }
  const sp = url.searchParams.get('speaker_id')
  if (sp) xs = xs.filter((s) => s.speaker?.id === sp)
  const rs = list(url, 'review_status')
  if (rs.length) xs = xs.filter((s) => rs.includes(s.review_status))
  const ts = list(url, 'text_source')
  if (ts.length) xs = xs.filter((s) => ts.includes(s.text_source))
  const q = url.searchParams.get('q')
  if (q) xs = xs.filter((s) => s.text.includes(q) || s.media_title.includes(q))
  const ms = num(url, 'min_snr')
  if (ms !== null) xs = xs.filter((s) => (s.snr ?? -99) >= ms)
  const md = num(url, 'min_dnsmos')
  if (md !== null) xs = xs.filter((s) => (s.dnsmos_ovrl ?? 0) >= md)
  const msr = num(url, 'min_speech_ratio')
  if (msr !== null) xs = xs.filter((s) => (s.speech_ratio ?? 0) >= msr)
  const fl = list(url, 'flags')
  if (fl.length) xs = xs.filter((s) => s.flags.some((f) => fl.includes(f)))
  const xf = list(url, 'exclude_flags')
  if (xf.length) xs = xs.filter((s) => !s.flags.some((f) => xf.includes(f)))
  const sort = url.searchParams.get('sort')
  if (sort === 'snr') xs = [...xs].sort((a, b) => (a.snr ?? 0) - (b.snr ?? 0))
  else if (sort === '-snr') xs = [...xs].sort((a, b) => (b.snr ?? 0) - (a.snr ?? 0))
  else if (sort === 'dnsmos') xs = [...xs].sort((a, b) => (a.dnsmos_ovrl ?? 0) - (b.dnsmos_ovrl ?? 0))
  else if (sort === '-duration') xs = [...xs].sort((a, b) => b.duration - a.duration)
  return xs
}

// ───────────── dataset preview (real computation over mock segments) ─────────────
function preview(f: T.DatasetFilters): T.DatasetPreview {
  const d = getDB()
  const all = allSegments()
  const excluded = { repeated: 0, auto_caption: 0, low_quality: 0, review: 0, speaker_cap: 0 }
  const kept: T.SegmentDetail[] = []
  for (const s of all) {
    const m = d.mediaById.get(s.media_id)!
    if (f.channel_ids.length && !f.channel_ids.includes(m.channel!.id)) continue
    if (f.speaker_ids.length && !(s.speaker && f.speaker_ids.includes(s.speaker.id))) continue
    if (f.licenses.length && !f.licenses.includes(m.license ?? 'youtube')) continue
    if (f.text_sources.length && !f.text_sources.includes(s.text_source)) {
      if (s.text_source === 'auto_caption') excluded.auto_caption++
      continue
    }
    if (f.review_status.length && !f.review_status.includes(s.review_status)) {
      excluded.review++
      continue
    }
    if (f.exclude_repeated && s.repeated_overlap > f.max_repeated_overlap) {
      excluded.repeated++
      continue
    }
    if (f.speaker_states.length && !f.speaker_states.includes(s.speaker_state)) {
      excluded.review++
      continue
    }
    const lowQ =
      (f.min_duration !== null && s.duration < f.min_duration) ||
      (f.max_duration !== null && s.duration > f.max_duration) ||
      (f.min_snr !== null && (s.snr ?? -99) < f.min_snr) ||
      (f.min_c50 !== null && (s.c50 ?? -99) < f.min_c50) ||
      (f.min_dnsmos_ovrl !== null && (s.dnsmos_ovrl ?? 0) < f.min_dnsmos_ovrl) ||
      (f.min_dnsmos_p808 !== null && (s.dnsmos_p808 ?? 0) < f.min_dnsmos_p808) ||
      (f.min_speech_ratio !== null && (s.speech_ratio ?? 0) < f.min_speech_ratio) ||
      (f.min_bandwidth_hz !== null && (m.q?.real_bandwidth_hz ?? 0) < f.min_bandwidth_hz) ||
      (f.exclude_upsampled && m.q?.upsampled_suspect) ||
      s.flags.some((x) => f.exclude_flags.includes(x))
    if (lowQ) {
      excluded.low_quality++
      continue
    }
    if (f.sex.length && !(s.profile?.sex && f.sex.includes(s.profile.sex))) continue
    if (f.age_groups.length && !(s.profile?.age_group && f.age_groups.includes(s.profile.age_group))) continue
    kept.push(s)
  }
  let final = kept
  if (f.max_sec_per_speaker) {
    const used = new Map<string, number>()
    final = []
    for (const s of kept) {
      const k = s.speaker?.id ?? `_${s.media_id}`
      const u = used.get(k) ?? 0
      if (u + s.duration > f.max_sec_per_speaker) {
        excluded.speaker_cap++
        continue
      }
      used.set(k, u + s.duration)
      final.push(s)
    }
  }
  const byCh = new Map<string, { channel_id: string; title: string; total_sec: number; segment_count: number }>()
  const bySex: Record<string, number> = { male: 0, female: 0, unknown: 0 }
  const sp = new Set<string>()
  for (const s of final) {
    const m = d.mediaById.get(s.media_id)!
    const c = byCh.get(m.channel!.id) ?? { channel_id: m.channel!.id, title: m.channel!.title, total_sec: 0, segment_count: 0 }
    c.total_sec += s.duration
    c.segment_count++
    byCh.set(m.channel!.id, c)
    bySex[s.profile?.sex ?? 'unknown'] += s.duration
    if (s.speaker) sp.add(s.speaker.id)
  }
  return {
    segment_count: final.length,
    total_sec: final.reduce((a, s) => a + s.duration, 0),
    speakers: sp.size,
    channels: byCh.size,
    by_channel: [...byCh.values()].sort((a, b) => b.total_sec - a.total_sec),
    by_sex: bySex,
    excluded,
  }
}

function hist(values: number[], lo: number, hi: number, step: number): T.HistBin[] {
  const bins: T.HistBin[] = []
  for (let b = lo; b < hi - 1e-9; b += step) bins.push({ bin: Math.round(b * 1000) / 1000, count: 0 })
  for (const v of values) {
    const i = Math.min(bins.length - 1, Math.max(0, Math.floor((v - lo) / step)))
    bins[i].count++
  }
  return bins
}

function diversity(cid: string | null): T.Diversity {
  const d = getDB()
  const segs = allSegments().filter((s) => !cid || d.mediaById.get(s.media_id)?.channel?.id === cid)
  const by_sex: Record<string, number> = {}
  const by_age: Record<string, number> = {}
  const by_emo: Record<string, number> = {}
  for (const s of segs) {
    const sex = s.profile?.sex ?? 'unknown'
    by_sex[sex] = (by_sex[sex] ?? 0) + s.duration
    const ag = s.profile?.age_group ?? 'unknown'
    by_age[ag] = (by_age[ag] ?? 0) + s.duration
    const em = topKey(s.profile?.emotion) ?? 'unknown'
    by_emo[em] = (by_emo[em] ?? 0) + s.duration
  }
  const sps = d.speakers.filter((s) => !cid || s.channel_ids.includes(cid))
  const sorted = [...sps].sort((a, b) => b.total_sec - a.total_sec)
  const xs = sorted.map((s) => s.total_sec).sort((a, b) => a - b)
  const n = xs.length
  const sum = xs.reduce((a, b) => a + b, 0)
  const gini = n && sum ? xs.reduce((a, x, i) => a + (2 * (i + 1) - n - 1) * x, 0) / (n * sum) : 0
  return {
    by_sex,
    by_age_group: by_age,
    by_emotion: by_emo,
    by_channel: d.channels
      .filter((c) => !cid || c.id === cid)
      .map((c) => {
        const cs = d.speakers.filter((s) => s.channel_ids.includes(c.id))
        const tot = cs.reduce((a, s) => a + s.total_sec, 0)
        return { channel_id: c.id, title: c.title, speakers: cs.length, total_sec: tot, top_speaker_share: tot ? Math.max(...cs.map((s) => s.total_sec)) / tot : 0 }
      })
      .filter((x) => x.total_sec > 0),
    speaker_hours: sorted.map((s) => ({ speaker_id: s.id, label: s.display_name ?? s.label, total_sec: s.total_sec })),
    gini: Math.round(gini * 1000) / 1000,
  }
}

function timeline(m: MockMedia): T.Timeline {
  const d = getDB()
  const segs = m.status === 'ready' || d.segments.has(m.id) ? genSegments(d, m) : []
  const dur = m.duration_sec ?? 60
  const N = Math.min(2000, Math.max(200, Math.round(dur * 4)))
  const hop = dur / N
  const r = rngFor(m.id.charCodeAt(0) * 131 + m.id.charCodeAt(3))
  const vad = new Array(N).fill(0).map(() => Math.max(0, Math.min(0.15, r.normal(0.04, 0.03))))
  const snr = new Array(N).fill(0).map(() => r.normal(4, 2))
  for (const s of segs) {
    const a = Math.floor(s.start_sec / hop)
    const b = Math.min(N - 1, Math.ceil(s.end_sec / hop))
    for (let i = a; i <= b; i++) {
      vad[i] = Math.max(0, Math.min(1, r.normal((s.speech_ratio ?? 0.8) + 0.08, 0.1)))
      snr[i] = r.normal(s.snr ?? 15, 3)
    }
  }
  const rgs = d.regions.get(m.id) ?? []
  const groups = d.repeated.get(m.channel!.id) ?? []
  // music-ish intro: low vad high snr
  for (const rg of rgs) {
    const a = Math.floor(rg.start / hop)
    const b = Math.min(N - 1, Math.ceil(rg.end / hop))
    for (let i = a; i <= b; i++) vad[i] = Math.min(vad[i], r.range(0.05, 0.35))
  }
  const spRegions: [number, number][] = segs.map((s) => [s.start_sec, s.end_sec])
  const diar = segs
    .filter((s) => s.local_speaker)
    .map((s) => ({ speaker: s.local_speaker!, start: s.start_sec, end: s.end_sec, overlap: s.speaker_state === 'overlap' }))
  return {
    duration: dur,
    hop_sec: hop,
    vad: vad.map((x) => Math.round(x * 1000) / 1000),
    snr: snr.map((x) => Math.round(x * 10) / 10),
    speech_regions: spRegions,
    diarization: diar,
    repeated: rgs.map((rg) => ({ id: rg.id, start: rg.start, end: rg.end, position: rg.position, excluded: groups.find((g) => g.group_id === rg.group_id)?.excluded ?? true })),
    cues: segs.map((s) => ({ start: s.start_sec, end: s.end_sec, text: s.original_text })),
    segments: segs.map(summary),
  }
}

function overview(): T.Overview {
  const d = getDB()
  const M = d.media
  const sumDur = (xs: MockMedia[]) => xs.reduce((a, m) => a + (m.duration_sec ?? 0), 0) / 3600
  const segs = allSegments()
  const segH = segs.reduce((a, s) => a + s.duration, 0) / 3600
  const revH = segs.filter((s) => s.review_status === 'passed').reduce((a, s) => a + s.duration, 0) / 3600
  const downloaded = M.filter((m) => ['downloaded', 'processing', 'ready'].includes(m.status))
  const ready = M.filter((m) => m.status === 'ready')
  const qd = (q: string) => d.jobs.filter((j) => j.queue === q && j.status === 'queued').length
  return {
    channels: d.channels.length,
    media: {
      listed: M.length,
      detailed: M.filter((m) => m.has_manual_captions !== null).length,
      downloaded: downloaded.length,
      ready: ready.length,
      failed: M.filter((m) => m.status === 'failed').length,
    },
    hours: {
      listed: sumDur(M),
      downloaded: sumDur(downloaded),
      speech: ready.reduce((a, m) => a + (m.q?.speech_sec ?? 0), 0) / 3600,
      segments: segH,
      reviewed: revH,
    },
    segments: {
      total: segs.length,
      passed: segs.filter((s) => s.review_status === 'passed').length,
      needs_fix: segs.filter((s) => s.review_status === 'needs_fix').length,
    },
    speakers: { candidates: d.speakers.length, verified: d.speakers.filter((s) => s.verified).length },
    storage_bytes: 412.6 * 1024 ** 3,
    workers: [
      { name: 'worker-fetch-1', queues: ['fetch'], alive: true, last_seen_at: new Date().toISOString(), current_job: (() => { const j = d.jobs.find((j) => j.queue === 'fetch' && j.status === 'running'); return j ? { id: j.id, kind: j.kind, media_title: j.media_title ?? null } : null })() },
      { name: 'worker-audio-1', queues: ['audio', 'export'], alive: true, last_seen_at: new Date().toISOString(), current_job: null },
      { name: 'worker-ml-1', queues: ['ml'], alive: true, last_seen_at: new Date().toISOString(), current_job: (() => { const j = d.jobs.find((j) => j.queue === 'ml' && j.status === 'running'); return j ? { id: j.id, kind: j.kind, media_title: j.media_title ?? null } : null })() },
      { name: 'legacy-ml', queues: ['ml'], alive: false, last_seen_at: new Date(Date.now() - 7 * 60_000).toISOString(), current_job: null },
    ],
    queue_depth: { fetch: qd('fetch'), audio: qd('audio'), ml: qd('ml'), export: qd('export') },
  }
}

const strip = <J extends T.Job>(j: J): T.Job => {
  const { _dur: _d, ...rest } = j as J & { _dur?: number }
  void _d
  return rest
}

function speakerSummary(s: DB['speakers'][number]): T.Speaker {
  const { sex: _s, age: _a, emo: _e, ...rest } = s
  void _s, _a, _e
  return rest
}

// ───────────── handlers ─────────────
export const handlers = [
  http.all(`${A}/*`, async ({ request }) => {
    getDB()
    tick(db)
    await lat()
    const path = new URL(request.url).pathname
    if (path === `${A}/auth/login` || path === `${A}/healthz`) return undefined
    if (path.startsWith(`${A}/audio/`)) return undefined
    if (!db.me) return err(401, '請先登入', 'not_authenticated')
    return undefined
  }),

  // auth
  http.post(`${A}/auth/login`, async ({ request }) => {
    const b = (await request.json()) as { username: string; password: string }
    const u = db.users.find((x) => x.username === b.username)
    if (!u || !b.password || b.password === 'wrong') return err(401, '帳號或密碼錯誤', 'invalid_credentials')
    if (!u.is_active) return err(403, '此帳號已停用', 'inactive')
    db.me = u.id
    u.last_login_at = new Date().toISOString()
    try {
      localStorage.removeItem(SESSION_KEY)
    } catch {
      /* noop */
    }
    return json(pub(u))
  }),
  http.post(`${A}/auth/logout`, () => {
    db.me = null
    try {
      localStorage.setItem(SESSION_KEY, '1')
    } catch {
      /* noop */
    }
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(`${A}/auth/me`, () => json(pub(user()!))),
  http.post(`${A}/auth/password`, async ({ request }) => {
    const b = (await request.json()) as { current_password: string; new_password: string }
    if (b.current_password === 'wrong') return err(400, '目前密碼不正確', 'bad_password')
    return new HttpResponse(null, { status: 204 })
  }),

  // users
  http.get(`${A}/users`, () => json(db.users.map(pub))),
  http.post(`${A}/users`, async ({ request }) => {
    const b = (await request.json()) as { username: string; display_name: string; password: string; role: T.Role }
    if (db.users.some((u) => u.username === b.username)) return err(409, '此帳號名稱已被使用', 'username_taken')
    const u = { id: crypto.randomUUID(), username: b.username, display_name: b.display_name, role: b.role, is_active: true, created_at: new Date().toISOString(), last_login_at: null, password: b.password }
    db.users.push(u)
    return json(pub(u), { status: 201 })
  }),
  http.patch(`${A}/users/:id`, async ({ request, params }) => {
    const u = db.users.find((x) => x.id === params.id)
    if (!u) return err(404, '找不到使用者', 'not_found')
    Object.assign(u, await request.json())
    return json(pub(u))
  }),

  // settings
  http.get(`${A}/settings`, () => json(db.settings)),
  http.put(`${A}/settings/cookies`, () => {
    db.settings.cookies = { present: true, updated_at: new Date().toISOString() }
    return json(db.settings.cookies)
  }),
  http.delete(`${A}/settings/cookies`, () => {
    db.settings.cookies = { present: false, updated_at: null }
    return new HttpResponse(null, { status: 204 })
  }),
  http.put(`${A}/settings/:key`, async ({ request, params }) => {
    const k = params.key as 'youtube' | 'pipeline' | 'quality_thresholds'
    ;(db.settings as unknown as Record<string, unknown>)[k] = await request.json()
    return json(db.settings[k])
  }),

  // sources
  http.post(`${A}/sources/resolve`, async ({ request }) => {
    const { url } = (await request.json()) as { url: string }
    await delay(500)
    if (/list=/.test(url)) {
      const pl = db.playlists[0]
      return json({ type: 'playlist', playlist: pl, job: strip(newJob(db, 'playlist.list', { channel_id: pl.channel_id, status: 'queued' })) })
    }
    if (/watch\?v=|youtu\.be\/|\/shorts\//.test(url)) {
      const m = db.media.find((x) => x.status === 'detailed' && x.has_manual_captions)!
      return json({ type: 'video', media: mediaSummary(m), channel: db.channels[m.channel_idx], job: strip(newJob(db, 'media.detail', { media_id: m.id, status: 'queued' })) })
    }
    if (/youtube\.com\/(@|channel\/|c\/)/.test(url)) {
      const c = db.channels[1]
      return json({ type: 'channel', channel: c, job: strip(newJob(db, 'channel.list', { channel_id: c.id, status: 'queued' })) })
    }
    return err(422, '無法辨識這個網址。請貼上 YouTube 影片、頻道或播放清單的連結。', 'unsupported_url')
  }),

  // channels
  http.get(`${A}/channels`, ({ request }) => {
    const url = new URL(request.url)
    const q = url.searchParams.get('q')?.toLowerCase()
    const xs = db.channels.filter((c) => !q || c.title.toLowerCase().includes(q) || (c.handle ?? '').toLowerCase().includes(q))
    return json(page(xs, url))
  }),
  http.get(`${A}/channels/:id/repeated`, ({ params }) => json(db.repeated.get(params.id as string) ?? [])),
  http.post(`${A}/channels/:id/dedup`, ({ params }) => json(strip(newJob(db, 'channel.dedup', { channel_id: params.id as string })))),
  http.post(`${A}/channels/:id/refresh`, ({ params }) => {
    const c = db.channels.find((x) => x.id === params.id)
    if (c) c.list_status = 'listing'
    return json(strip(newJob(db, 'channel.list', { channel_id: params.id as string })))
  }),
  http.post(`${A}/channels/:id/detail`, ({ params }) => json({ queued: db.media.filter((m) => m.channel?.id === params.id && m.has_manual_captions === null).length })),
  http.get(`${A}/channels/:id/delete-impact`, ({ params }) => {
    const ms = db.media.filter((m) => m.channel?.id === params.id)
    const dl = ms.filter((m) => ['downloaded', 'processing', 'ready'].includes(m.status))
    const segs = dl.reduce((a, m) => a + (m.segment_count ?? 0), 0)
    return json({
      media_total: ms.length, playlists: db.playlists.filter((p) => p.channel_id === params.id).length,
      downloaded_media: dl.length, downloaded_sec: dl.reduce((a, m) => a + (m.duration_sec ?? 0), 0),
      segments: segs, reviewed_segments: Math.round(segs * 0.18), storage_bytes: dl.reduce((a, m) => a + (m.duration_sec ?? 0) * 210_000, 0),
      active_jobs: db.jobs.filter((j) => j.channel_id === params.id && ['queued', 'running'].includes(j.status)).length,
      datasets_referencing: 1, requires_confirmation: dl.length > 0,
    } satisfies T.ChannelDeleteImpact)
  }),
  http.delete(`${A}/channels/:id`, ({ params, request }) => {
    const c = db.channels.find((x) => x.id === params.id)
    if (!c) return json({ detail: '找不到頻道', code: 'not_found' }, { status: 404 })
    const ms = db.media.filter((m) => m.channel?.id === c.id)
    const hasDl = ms.some((m) => ['downloaded', 'processing', 'ready'].includes(m.status))
    if (hasDl && new URL(request.url).searchParams.get('confirm') !== c.title) return json({ detail: '請輸入頻道名稱確認', code: 'confirmation_required' }, { status: 409 })
    db.channels = db.channels.filter((x) => x.id !== c.id)
    db.media = db.media.filter((m) => m.channel?.id !== c.id)
    for (const m of ms) db.mediaById.delete(m.id)
    return json({ objects_deleted: ms.length })
  }),
  http.get(`${A}/channels/:id`, ({ params }) => {
    const c = db.channels.find((x) => x.id === params.id)
    return c ? json(c) : err(404, '找不到頻道', 'not_found')
  }),
  http.patch(`${A}/channels/:id`, async ({ request, params }) => {
    const c = db.channels.find((x) => x.id === params.id)!
    Object.assign(c, await request.json())
    return json(c)
  }),
  http.get(`${A}/playlists`, ({ request }) => {
    const url = new URL(request.url)
    const cid = url.searchParams.get('channel_id')
    return json(page(db.playlists.filter((p) => !cid || p.channel_id === cid), url))
  }),
  http.get(`${A}/playlists/:id`, ({ params }) => json(db.playlists.find((p) => p.id === params.id))),
  http.patch(`${A}/repeated/:gid`, async ({ request, params }) => {
    const b = (await request.json()) as { excluded: boolean }
    for (const gs of db.repeated.values())
      for (const g of gs)
        if (g.group_id === params.gid) {
          g.excluded = b.excluded
          return json(g)
        }
    return err(404, '找不到重複片段群組', 'not_found')
  }),

  // media
  http.get(`${A}/media`, ({ request }) => {
    const url = new URL(request.url)
    const f = mediaFilterFromURL(url)
    const xs = sortMedia(db.media.filter((m) => matchMedia(m, f)), url.searchParams.get('sort'))
    return json({ ...page(xs.map(mediaSummary), url), facets: facets(f) })
  }),
  http.post(`${A}/media/download`, async ({ request }) => {
    const b = (await request.json()) as { media_ids?: string[]; filter?: MF; include_auto: boolean }
    const targets = b.media_ids ? b.media_ids.map((id) => db.mediaById.get(id)!).filter(Boolean) : db.media.filter((m) => matchMedia(m, b.filter ?? {}))
    let queued = 0
    let skipped = 0
    for (const m of targets) {
      const ok = m.has_manual_captions || (b.include_auto && m.has_auto_captions)
      if (!ok || !['listed', 'detailed', 'failed'].includes(m.status)) {
        skipped++
        continue
      }
      m.status = 'queued'
      newJob(db, 'media.download', { media_id: m.id })
      queued++
    }
    return json({ queued, skipped })
  }),
  http.get(`${A}/media/:id/timeline`, ({ params }) => {
    const m = db.mediaById.get(params.id as string)
    return m ? json(timeline(m)) : err(404, '找不到影片', 'not_found')
  }),
  http.get(`${A}/media/:id/quality`, ({ params }) => json(db.mediaById.get(params.id as string)?.q ?? null)),
  http.post(`${A}/media/:id/retry`, ({ params }) => {
    const m = db.mediaById.get(params.id as string)!
    const kind = (m.stage ?? 'media.download') as T.JobKind
    m.status = 'queued'
    m.failure_code = null
    return json(strip(newJob(db, PIPE.includes(kind) ? kind : 'media.download', { media_id: m.id })))
  }),
  http.post(`${A}/media/:id/reprocess`, async ({ request, params }) => {
    const b = (await request.json()) as { from_step: T.JobKind }
    const m = db.mediaById.get(params.id as string)!
    m.status = 'processing'
    m.stage = b.from_step
    return json(strip(newJob(db, b.from_step, { media_id: m.id })))
  }),
  http.get(`${A}/media/:id`, ({ params }) => {
    const m = db.mediaById.get(params.id as string)
    if (!m) return err(404, '找不到影片', 'not_found')
    if (m.status === 'ready') genSegments(db, m)
    const det: T.MediaDetail = {
      ...mediaSummary(m),
      description: m.description,
      tags: ['台語', m.channel!.title],
      categories: ['Education'],
      chapters: m.kind === 'video' && (m.duration_sec ?? 0) > 600 ? [
        { title: '開場', start_time: 0, end_time: 30 },
        { title: '正文', start_time: 30, end_time: (m.duration_sec ?? 600) - 40 },
        { title: '結尾', start_time: (m.duration_sec ?? 600) - 40, end_time: m.duration_sec ?? 600 },
      ] : [],
      language: 'zh-TW',
      like_count: m.like_count,
      comment_count: Math.round((m.like_count ?? 0) * 0.08),
      availability: 'public',
      age_limit: 0,
      metadata: { uploader: m.channel!.title, fps: 30, resolution: m.kind === 'short' ? '1080x1920' : '1920x1080' },
      failure_message: m.status === 'failed' ? db.jobs.find((j) => j.media_id === m.id && j.status === 'failed')?.error_message ?? null : null,
      jobs: db.jobs.filter((j) => j.media_id === m.id).map(strip),
      tracks: [
        ...m.manual_caption_langs.map((l) => ({ id: crypto.randomUUID(), language: l, kind: 'manual' as const })),
        ...m.auto_caption_langs.map((l) => ({ id: crypto.randomUUID(), language: l, kind: 'auto' as const })),
      ],
      quality: m.q,
    }
    return json(det)
  }),

  // segments
  http.get(`${A}/segments`, ({ request }) => {
    const url = new URL(request.url)
    return json(page(filterSegments(url).map(summary), url))
  }),
  http.get(`${A}/segments/:id/similar`, ({ params }) => {
    const s = db.segById.get(params.id as string)
    if (!s) return json([])
    const r = rngFor(s.id.charCodeAt(0) * 17 + s.id.charCodeAt(5))
    const pool = allSegments().filter((x) => x.id !== s.id && x.speaker && x.speaker.id === s.speaker?.id)
    const other = allSegments().filter((x) => x.id !== s.id)
    const out = Array.from({ length: 6 }, (_, i) => {
      const seg = pool.length && i < 4 ? pool[Math.floor(r.next() * pool.length)] : other[Math.floor(r.next() * other.length)]
      return { segment: summary(seg), similarity: Math.round((i < 4 ? r.range(0.78, 0.94) : r.range(0.55, 0.7)) * 1000) / 1000 }
    }).sort((a, b) => b.similarity - a.similarity)
    return json(out)
  }),
  http.put(`${A}/segments/:id/speaker`, async ({ request, params }) => {
    const s = db.segById.get(params.id as string)!
    const b = (await request.json()) as { speaker_name: string }
    s.speaker = { id: s.speaker?.id ?? crypto.randomUUID(), label: s.speaker?.label ?? 'manual', display_name: b.speaker_name }
    return json(s)
  }),
  http.delete(`${A}/segments/:id/speaker`, () => new HttpResponse(null, { status: 204 })),
  http.get(`${A}/segments/:id`, ({ params }) => {
    const s = db.segById.get(params.id as string)
    return s ? json(s) : err(404, '找不到音段', 'not_found')
  }),
  http.patch(`${A}/segments/:id`, async ({ request, params }) => {
    const s = db.segById.get(params.id as string)
    if (!s) return err(404, '找不到音段', 'not_found')
    const b = (await request.json()) as T.SegmentPatch
    const before: Record<string, unknown> = {}
    for (const k of Object.keys(b)) before[k] = (s as unknown as Record<string, unknown>)[k]
    if (b.corrected_text !== undefined) {
      s.corrected_text = b.corrected_text
      s.text = b.corrected_text ?? s.original_text
      if (b.corrected_text && b.corrected_text !== s.original_text) s.text_source = 'human_revision'
    }
    if (b.review_status) {
      s.review_status = b.review_status
      const u = user()!
      s.reviewed_by = { id: u.id, display_name: u.display_name }
      s.reviewed_at = new Date().toISOString()
    }
    if (b.review_note !== undefined) s.review_note = b.review_note
    if (b.speaker_state) s.speaker_state = b.speaker_state
    if (b.start_sec !== undefined || b.end_sec !== undefined) {
      s.start_sec = b.start_sec ?? s.start_sec
      s.end_sec = b.end_sec ?? s.end_sec
      s.duration = Math.round((s.end_sec - s.start_sec) * 100) / 100
      s.revision += 1
      if (!s.flags.includes('boundary_adjusted')) s.flags = [...s.flags, 'boundary_adjusted']
    }
    const u = user()!
    s.history = [{ id: crypto.randomUUID(), at: new Date().toISOString(), user: { id: u.id, display_name: u.display_name }, action: 'update', before, after: b as Record<string, unknown> }, ...s.history]
    return json(s)
  }),

  // audio
  http.get(`${A}/audio/segments/:id`, ({ params, request }) => {
    const s = db.segById.get(params.id as string)
    const pad = Number(new URL(request.url).searchParams.get('pad') ?? 0.3)
    const dur = s ? s.duration + pad * 2 : 3
    const sp = s?.speaker ? db.speakers.find((x) => x.id === s.speaker!.id) : null
    const f0 = sp ? (sp.sex === 'female' ? 210 : 115) * (sp.age > 65 ? 0.92 : 1) : 150
    const wav = synthWav((s?.id.charCodeAt(0) ?? 7) * 997 + (s?.id.charCodeAt(2) ?? 3), dur, s?.snr ?? 20, f0)
    return new HttpResponse(wav, { headers: { 'Content-Type': 'audio/wav', 'Content-Length': String(wav.byteLength) } })
  }),

  // speakers
  http.get(`${A}/speakers/runs`, () => json(db.runs)),
  http.get(`${A}/speakers/diversity`, ({ request }) => json(diversity(new URL(request.url).searchParams.get('channel_id')))),
  http.post(`${A}/speakers/cluster`, async ({ request }) => {
    const b = (await request.json()) as { threshold: number }
    const j = newJob(db, 'speakers.cluster', {}) as T.Job & { _threshold?: number }
    j._threshold = b.threshold
    return json(strip(j))
  }),
  http.get(`${A}/speakers`, ({ request }) => {
    const url = new URL(request.url)
    const cid = url.searchParams.get('channel_id')
    const q = url.searchParams.get('q')
    let xs = db.speakers.filter((s) => !cid || s.channel_ids.includes(cid))
    if (q) xs = xs.filter((s) => (s.display_name ?? '').includes(q) || s.label.includes(q))
    const sort = url.searchParams.get('sort') ?? '-total_sec'
    if (sort === '-segment_count') xs = [...xs].sort((a, b) => b.segment_count - a.segment_count)
    if (sort === 'label') xs = [...xs].sort((a, b) => a.label.localeCompare(b.label))
    return json(page(xs.map(speakerSummary), url))
  }),
  http.get(`${A}/speakers/:id`, ({ params }) => {
    const s = db.speakers.find((x) => x.id === params.id)
    if (!s) return err(404, '找不到語者', 'not_found')
    const segs = allSegments().filter((x) => x.speaker?.id === s.id)
    const byCh = new Map<string, { id: string; title: string; total_sec: number }>()
    for (const x of segs) {
      const m = db.mediaById.get(x.media_id)!
      const c = byCh.get(m.channel!.id) ?? { id: m.channel!.id, title: m.channel!.title, total_sec: 0 }
      c.total_sec += x.duration
      byCh.set(c.id, c)
    }
    const det: T.SpeakerDetail = {
      ...speakerSummary(s),
      segments: [...segs].sort((a, b) => (b.dnsmos_ovrl ?? 0) - (a.dnsmos_ovrl ?? 0)).slice(0, 50).map(summary),
      channels: [...byCh.values()],
    }
    return json(det)
  }),
  http.patch(`${A}/speakers/:id`, async ({ request, params }) => {
    const s = db.speakers.find((x) => x.id === params.id)!
    Object.assign(s, await request.json())
    return json(speakerSummary(s))
  }),

  // quality
  http.get(`${A}/quality/summary`, ({ request }) => {
    const cid = new URL(request.url).searchParams.get('channel_id')
    const ms = db.media.filter((m) => m.status === 'ready' && m.q && (!cid || m.channel?.id === cid))
    const q = ms.map((m) => m.q!)
    const count = (fn: (x: T.MediaQuality) => string) => q.reduce<Record<string, number>>((a, x) => ((a[fn(x)] = (a[fn(x)] ?? 0) + 1), a), {})
    const res: T.QualitySummary = {
      histograms: {
        snr: hist(q.map((x) => x.snr_mean ?? 0), 0, 40, 2.5),
        dnsmos_ovrl: hist(q.map((x) => x.dnsmos_ovrl ?? 0), 1, 4.6, 0.2),
        c50: hist(q.map((x) => x.c50_mean ?? 0), 0, 60, 5),
        bandwidth_hz: hist(q.map((x) => x.real_bandwidth_hz ?? 0), 0, 24000, 1000),
        speech_ratio: hist(q.map((x) => x.speech_ratio ?? 0), 0, 1, 0.05),
      },
      sample_rates: count((x) => String(x.sample_rate)),
      codecs: count((x) => x.codec ?? 'unknown'),
      upsampled_suspect: q.filter((x) => x.upsampled_suspect).length,
      media_count: q.length,
    }
    return json(res)
  }),

  // datasets
  http.get(`${A}/datasets`, () => json(db.datasets.map((d) => ({ ...d, ...pick(preview(d.filters)) })))),
  http.post(`${A}/datasets/preview`, async ({ request }) => {
    const b = (await request.json()) as { filters: T.DatasetFilters }
    return json(preview(b.filters))
  }),
  http.post(`${A}/datasets`, async ({ request }) => {
    const b = (await request.json()) as { name: string; description: string; filters: T.DatasetFilters }
    const u = user()!
    const d: T.Dataset = { id: crypto.randomUUID(), ...b, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), created_by: { id: u.id, display_name: u.display_name }, last_export: null }
    db.datasets.unshift(d)
    return json(d, { status: 201 })
  }),
  http.get(`${A}/datasets/:id/exports`, ({ params }) =>
    json(db.exports.filter((e) => e.dataset_id === params.id).sort((a, b) => b.created_at.localeCompare(a.created_at)).map(({ _start: _s, ...e }) => (void _s, e))),
  ),
  http.post(`${A}/datasets/:id/export`, async ({ request, params }) => {
    const b = (await request.json()) as { format: 'jsonl' | 'csv'; include_audio: boolean }
    const d = db.datasets.find((x) => x.id === params.id)!
    const p = preview(d.filters)
    const e = { id: crypto.randomUUID(), dataset_id: d.id, status: 'queued' as const, format: b.format, include_audio: b.include_audio, segment_count: p.segment_count, total_sec: p.total_sec, stats: {}, progress: 0, size_bytes: null, files: [], created_at: new Date().toISOString(), finished_at: null, _start: Date.now() }
    db.exports.push(e)
    d.last_export = e
    const j = newJob(db, 'dataset.export', { _dur: 9000 })
    j.media_title = `${d.name}（${b.format.toUpperCase()}）`
    const { _start: _s, ...out } = e
    void _s
    return json(out, { status: 201 })
  }),
  http.get(`${A}/datasets/:id`, ({ params }) => {
    const d = db.datasets.find((x) => x.id === params.id)
    return d ? json(d) : err(404, '找不到資料集', 'not_found')
  }),
  http.patch(`${A}/datasets/:id`, async ({ request, params }) => {
    const d = db.datasets.find((x) => x.id === params.id)!
    Object.assign(d, await request.json(), { updated_at: new Date().toISOString() })
    return json(d)
  }),
  http.delete(`${A}/datasets/:id`, ({ params }) => {
    db.datasets = db.datasets.filter((x) => x.id !== params.id)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(`${A}/exports/:id/download/:name`, ({ params }) =>
    new HttpResponse(`# VOTI export ${params.id}\n(mock file: ${params.name})\n`, { headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Content-Disposition': `attachment; filename="${params.name}"` } }),
  ),

  // jobs & stats
  http.get(`${A}/jobs`, ({ request }) => {
    const url = new URL(request.url)
    const st = list(url, 'status')
    const kd = list(url, 'kind')
    const mid = url.searchParams.get('media_id')
    const order = (j: T.Job) => (j.status === 'running' ? 3 : j.status === 'queued' ? 2 : 1)
    let xs = db.jobs.filter((j) => (!st.length || st.includes(j.status)) && (!kd.length || kd.includes(j.kind)) && (!mid || j.media_id === mid))
    xs = [...xs].sort((a, b) => order(b) - order(a) || (b.finished_at ?? b.started_at ?? b.created_at).localeCompare(a.finished_at ?? a.started_at ?? a.created_at))
    const counts: Record<string, number> = {}
    for (const j of db.jobs) counts[j.status] = (counts[j.status] ?? 0) + 1
    return json({ ...page(xs.map(strip), url), counts })
  }),
  http.post(`${A}/jobs/:id/retry`, ({ params }) => {
    const j = db.jobs.find((x) => x.id === params.id)!
    j.status = 'queued'
    j.attempts += 1
    j.error_code = null
    j.error_message = null
    j.finished_at = null
    j.progress = 0
    const m = j.media_id ? db.mediaById.get(j.media_id) : null
    if (m) {
      m.status = 'queued'
      m.failure_code = null
    }
    return json(strip(j))
  }),
  http.post(`${A}/jobs/:id/cancel`, ({ params }) => {
    const j = db.jobs.find((x) => x.id === params.id)!
    j.status = 'cancelled'
    j.finished_at = new Date().toISOString()
    return json(strip(j))
  }),
  http.get(`${A}/stats/overview`, () => json(overview())),
  http.get(`${A}/healthz`, () => json({ ok: true })),
]

function pick(p: T.DatasetPreview) {
  return { segment_count: p.segment_count, total_sec: p.total_sec }
}
void bool
