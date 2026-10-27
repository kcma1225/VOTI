/* Mock hard-subtitle OCR: events derived from a media's segments (paired) plus rejected events in the gaps, and SVG
 * "crops" that look like burned-in subtitles over a video frame. Everything is synthetic. */
import type * as T from '@/api/types'
import { NOW, rngFor } from './data'

export interface MockOcr {
  summary: T.OcrSummary
  events: T.OcrEvent[]
  trackId: string
  videoAvailable: boolean
}

const W = 1280
const H = 720
const FPS = 5
const round = (x: number, d = 2) => Math.round(x * 10 ** d) / 10 ** d
const iso = (ms: number) => new Date(ms).toISOString()
const TERMINAL = /[。！？]$/
// a few Traditional → Simplified pairs: burned-in subtitles of some uploads are Simplified (converted on pairing)
const T2S: Record<string, string> = { 們: '们', 個: '个', 這: '这', 說: '说', 來: '来', 時: '时', 會: '会', 國: '国', 灣: '湾', 語: '语', 講: '讲', 點: '点', 頭: '头', 魚: '鱼', 歡: '欢', 遠: '远', 錢: '钱', 節: '节', 間: '间', 氣: '气', 邊: '边', 飯: '饭' }
const toSimplified = (s: string) => Array.from(s, (c) => T2S[c] ?? c).join('')

/** Sentences of a segment text, each one subtitle line (terminal punctuation dropped, as on screen). */
function lines(text: string): string[] {
  const parts = text.split(/(?<=[。！？])/).map((x) => x.trim()).filter(Boolean)
  const out: string[] = []
  for (const p of parts) {
    const bare = p.replace(TERMINAL, '')
    // long sentences are shown as two subtitle lines at their comma
    if (bare.length > 18 && bare.includes('，')) {
      const i = bare.indexOf('，')
      out.push(bare.slice(0, i), bare.slice(i + 1))
    } else out.push(bare)
  }
  return out.length ? out : [text]
}

const TITLE_CARDS = ['台語新聞', '逐工一句台語', '今仔日的主題', '節目贊助：台灣母語基金會', '訂閱・分享・開啟小鈴鐺']
const CLIP_CAPTIONS = ['這馬咱來看現場的情形', '記者佇現場為你報導', '民眾講真歡喜', '（訪問片段）伊講真好食']
const NOISE = ['喔', '嗯', '★', '1', '台', '哈']

/** Build OCR events for an ocr_caption media from its segments (mutates the segments' cue_ids / track_id). */
export function genOcr(m: T.MediaSummary, segs: T.SegmentDetail[], idx: number): MockOcr {
  const r = rngFor(31_337 + idx * 101)
  const trackId = r.uuid()
  const dur = m.duration_sec ?? 600
  type Draft = Omit<T.OcrEvent, 'idx' | 'crop_url'>
  const drafts: Draft[] = []
  const box = (text: string, track: number, nLines = 1): number[] => {
    const per = track === 1 ? 26 : 34
    const longest = nLines > 1 ? Math.ceil(text.length / nLines) : text.length
    const w = Math.min(W - 80, longest * per + 36)
    const h = (track === 1 ? 34 : 44) * nLines + 8
    const y1 = track === 1 ? 600 : track === 2 ? 120 : 676
    return [Math.round((W - w) / 2), y1 - h, Math.round((W + w) / 2), y1]
  }
  const base = (o: Partial<Draft> & Pick<Draft, 'start' | 'end' | 'raw_text' | 'text' | 'status'>): Draft => ({
    cue_id: r.uuid(),
    s2t_converted: false,
    track: 0,
    style: 0,
    bbox: box(o.text, o.track ?? 0),
    lines: 1,
    conf: round(r.range(0.93, 0.995), 3),
    n_frames: Math.max(2, Math.round((o.end - o.start) * FPS)),
    agreement: round(r.range(0.72, 1), 2),
    reason: null,
    speech_sec: 0,
    paired_start: null,
    paired_end: null,
    ...o,
  })

  // paired events: one per subtitle line of every segment, split by character count
  segs.forEach((s, si) => {
    const ls = lines(s.original_text)
    const total = ls.reduce((a, x) => a + x.length, 0) || 1
    let t = s.start_sec
    const cues: string[] = []
    ls.forEach((text, li) => {
      const d = ((s.end_sec - s.start_sec) * text.length) / total
      const start = round(t - r.range(0.05, 0.3), 2)
      const end = round(t + d + r.range(0.05, 0.35), 2)
      let raw = text
      let s2t = false
      const k = (si * 7 + li) % 23
      if (k === 4) {
        raw = toSimplified(text)
        s2t = raw !== text
      } else if (k === 9) raw = `${text}（無法證實）`
      else if (k === 15) raw = `『${text}』`
      const nLines = text.length > 14 ? 2 : 1
      const e = base({
        start: Math.max(0, start),
        end,
        raw_text: raw,
        text: k === 15 ? `「${text}」` : text,
        s2t_converted: s2t,
        status: 'paired',
        lines: nLines,
        bbox: box(text, 0, nLines),
        speech_sec: round(d * r.range(0.78, 0.97), 2),
        paired_start: round(t, 2),
        paired_end: round(t + d, 2),
        conf: si % 19 === 6 ? round(r.range(0.86, 0.9), 3) : round(r.range(0.93, 0.995), 3),
      })
      cues.push(e.cue_id!)
      drafts.push(e)
      t += d
    })
    s.cue_ids = cues
    s.track_id = trackId
  })

  // rejected events
  const reasons: T.OcrRejectReason[] = ['no_speech', 'little_speech', 'rate_mismatch', 'concurrent_tracks', 'low_ocr_confidence', 'too_short_text', 'no_speech', 'too_long', 'too_short']
  drafts.push(base({ start: 0.4, end: round(r.range(13, 16), 2), raw_text: TITLE_CARDS[idx % TITLE_CARDS.length], text: TITLE_CARDS[idx % TITLE_CARDS.length], status: 'rejected', reason: 'static_text', track: 2, style: 2, speech_sec: round(r.range(4, 9), 2), bbox: box(TITLE_CARDS[idx % TITLE_CARDS.length], 2) }))
  let k = 0
  for (let i = 1; i < segs.length; i++) {
    const a = segs[i - 1].end_sec
    const b = segs[i].start_sec
    const reason = reasons[k % reasons.length]
    const gap = b - a
    if (reason !== 'concurrent_tracks' && reason !== 'rate_mismatch' && reason !== 'low_ocr_confidence' && gap < 1.2) continue
    if (!r.chance(0.42)) continue
    k++
    const at = reason === 'concurrent_tracks' || reason === 'rate_mismatch' || reason === 'low_ocr_confidence' ? segs[i].start_sec + 0.2 : a + 0.15
    const len = reason === 'too_short' ? 0.4 : reason === 'too_long' ? r.range(9, 11) : reason === 'concurrent_tracks' ? r.range(2, 3.4) : Math.max(0.6, Math.min(2.6, gap - 0.3))
    const text = reason === 'too_short_text' ? r.pick(NOISE) : reason === 'concurrent_tracks' ? r.pick(CLIP_CAPTIONS) : reason === 'rate_mismatch' ? '各位觀眾朋友大家好，歡迎收看今仔日的節目，咱這擺欲講的主題' : r.pick(segs).original_text.replace(TERMINAL, '').slice(0, 12)
    const track = reason === 'concurrent_tracks' ? 1 : 0
    drafts.push(
      base({
        start: round(at, 2),
        end: round(at + len, 2),
        raw_text: text,
        text,
        status: 'rejected',
        reason,
        track,
        style: track,
        bbox: box(text, track),
        conf: reason === 'low_ocr_confidence' ? round(r.range(0.58, 0.82), 3) : round(r.range(0.88, 0.99), 3),
        agreement: reason === 'low_ocr_confidence' ? round(r.range(0.3, 0.55), 2) : round(r.range(0.7, 1), 2),
        speech_sec: reason === 'no_speech' ? round(r.range(0, 0.12), 2) : reason === 'little_speech' ? round(len * r.range(0.15, 0.4), 2) : round(len * r.range(0.6, 0.9), 2),
      }),
    )
  }
  drafts.sort((x, y) => x.start - y.start)
  const events: T.OcrEvent[] = drafts.map((d, i) => ({ ...d, idx: i, crop_url: `/api/media/${m.id}/ocr/crops/${i}.webp` }))
  const paired = events.filter((e) => e.status === 'paired')
  const rejected: Record<string, number> = {}
  for (const e of events) if (e.status === 'rejected' && e.reason) rejected[e.reason] = (rejected[e.reason] ?? 0) + 1
  const trackingFrames = Math.round(dur * FPS * 1.6)
  const at = NOW - 3 * 86400_000 + 3600_000
  const summary: T.OcrSummary = {
    status: 'done',
    requested: 'auto_no_captions',
    queued_at: iso(at - 25 * 60_000),
    video_bytes: Math.round(dur * 82_000),
    video_format: 'mp4 · avc1 · 1280×720',
    at: iso(at),
    model: 'PP-OCRv5 server',
    device: 'cuda',
    fps: FPS,
    styles: [
      { id: 0, cy: 650, h: 38, fill: [246, 246, 246], edge: [18, 18, 18] },
      { id: 1, cy: 583, h: 28, fill: [255, 222, 70], edge: [30, 30, 30] },
    ],
    tracks: 2,
    events: events.length,
    paired: paired.length,
    rejected,
    paired_sec: round(paired.reduce((a, e) => a + ((e.paired_end ?? e.end) - (e.paired_start ?? e.start)), 0), 1),
    crops: events.length,
    text_converted: events.filter((e) => e.s2t_converted).length,
    video_deleted: true,
    elapsed_sec: round(dur * 0.24, 1),
    stats: {
      frame_size: [W, H], duration: dur, discovery_step_sec: 2, discovery_frames: Math.min(900, Math.round(dur / 2)), discovery_boxes: Math.round(dur * 1.7),
      styles_found: 7, styles_accepted: 2, tracks: 2, tracking_frames: trackingFrames, ocr_frames: Math.round(trackingFrames * 0.38),
      reused_frames: Math.round(trackingFrames * 0.62), discovery_sec: round(dur * 0.05, 1), tracking_sec: round(dur * 0.17, 1),
      events: events.length, paired: paired.length, rejected: events.length - paired.length,
    },
    speech_source: 'frames_quality',
    track_id: trackId,
  }
  return { summary, events, trackId, videoAvailable: false }
}

/** OcrResult (GET /media/{id}/ocr) of a mock media. */
export function ocrResult(o: MockOcr | undefined, jobActive: boolean): T.OcrResult {
  if (!o) return { status: 'none', summary: {}, model: null, frame_size: null, fps: null, styles: [], tracks: [], events: [], total: 0, job_active: jobActive, video_available: false }
  const done = o.summary.status === 'done'
  return {
    status: o.summary.status,
    summary: o.summary,
    model: done ? 'PP-OCRv5 server' : null,
    frame_size: done ? [W, H] : null,
    fps: o.summary.fps ?? null,
    styles: done ? (o.summary.styles ?? []).map((s) => ({ ...s, accepted: true, reasons: [] })) : [],
    tracks: done ? [{ track: 0, styles: [0], band: [600, 690] }, { track: 1, styles: [1], band: [550, 610] }] : [],
    events: o.events,
    total: o.events.length,
    job_active: jobActive,
    video_available: o.videoAvailable,
  }
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
/** A crop image (SVG): the event's subtitle text in its track's style over a blurred "video" background. */
export function cropSvg(e: T.OcrEvent): string {
  const r = rngFor(e.idx * 7919 + 13)
  const nLines = e.lines > 1 ? 2 : 1
  const chars = Array.from(e.raw_text)
  const per = Math.ceil(chars.length / nLines)
  const rows = Array.from({ length: nLines }, (_, i) => chars.slice(i * per, (i + 1) * per).join(''))
  const fs = e.track === 1 ? 26 : 32
  const w = Math.max(120, Math.max(...rows.map((x) => Array.from(x).length)) * fs + 48)
  const h = Math.min(96, nLines * (fs + 10) + 22)
  const hue = Math.round(r.range(0, 360))
  const bg = `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 22% 26%)"/><stop offset="1" stop-color="hsl(${(hue + 50) % 360} 28% 40%)"/></linearGradient><filter id="b"><feGaussianBlur stdDeviation="9"/></filter></defs>
<rect width="${w}" height="${h}" fill="url(#g)"/>
<g filter="url(#b)" opacity=".7"><ellipse cx="${Math.round(r.range(0, w))}" cy="${Math.round(r.range(0, h))}" rx="${Math.round(r.range(40, 120))}" ry="${Math.round(r.range(20, 50))}" fill="hsl(${(hue + 160) % 360} 35% 55%)"/><ellipse cx="${Math.round(r.range(0, w))}" cy="${Math.round(r.range(0, h))}" rx="${Math.round(r.range(30, 90))}" ry="${Math.round(r.range(15, 40))}" fill="hsl(${(hue + 20) % 360} 20% 75%)"/></g>`
  const font = `font-family="'Noto Sans CJK TC','Noto Sans TC','PingFang TC','Microsoft JhengHei',sans-serif" font-weight="700" font-size="${fs}" text-anchor="middle"`
  const y0 = (h - nLines * (fs + 10)) / 2 + fs
  const text = rows
    .map((row, i) => {
      const y = Math.round(y0 + i * (fs + 10))
      if (e.track === 1) return `<text x="${w / 2}" y="${y}" ${font} fill="#ffde46">${esc(row)}</text>`
      if (e.track === 2) return `<text x="${w / 2}" y="${y}" ${font} fill="#ffffff">${esc(row)}</text>`
      return `<text x="${w / 2}" y="${y}" ${font} fill="#f6f6f6" stroke="#121212" stroke-width="6" stroke-linejoin="round" paint-order="stroke">${esc(row)}</text>`
    })
    .join('')
  const plate =
    e.track === 1
      ? `<rect x="10" y="${(h - nLines * (fs + 10)) / 2 - 4}" width="${w - 20}" height="${nLines * (fs + 10) + 8}" rx="4" fill="rgba(0,0,0,.62)"/>`
      : e.track === 2
        ? `<rect x="6" y="6" width="${w - 12}" height="${h - 12}" rx="6" fill="#1d5fa8" opacity=".92"/>`
        : ''
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${bg}${plate}${text}</svg>`
}
