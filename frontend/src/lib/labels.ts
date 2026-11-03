import { getLang, localized, msgs } from '@/i18n'
import type { Job, JobKind, JobStatus, ListStatus, MediaStatus, ReviewStatus, SpeakerState } from '@/api/types'

export type Tone = 'neutral' | 'primary' | 'info' | 'success' | 'warning' | 'danger'

const L = () => msgs().labels
const withTone = <K extends string>(labels: Record<K, string>, tones: Record<K, Tone>) =>
  Object.fromEntries(Object.entries(tones).map(([k, tone]) => [k, { label: labels[k as K], tone }])) as Record<K, { label: string; tone: Tone }>

/* Every map below is a language-aware view (see i18n `localized`): reading a key returns the active language's label. */
export const mediaStatus: Record<MediaStatus, { label: string; tone: Tone }> = localized(() =>
  withTone(L().mediaStatus, {
    listed: 'neutral', detailed: 'neutral', queued: 'info', downloading: 'primary', downloaded: 'info',
    processing: 'primary', ready: 'success', failed: 'danger', blocked: 'warning', skipped: 'neutral',
  }),
)

export const jobStatus: Record<JobStatus, { label: string; tone: Tone }> = localized(() =>
  withTone(L().jobStatus, {
    queued: 'neutral', running: 'primary', completed: 'success', failed: 'danger', blocked: 'warning', cancelled: 'neutral', interrupted: 'warning',
  }),
)

export const jobKind: Record<JobKind, string> = localized(() => L().jobKind)

export const listStatus: Record<ListStatus, { label: string; tone: Tone }> = localized(() =>
  withTone(L().listStatus, { new: 'neutral', queued: 'info', listing: 'primary', listed: 'success', failed: 'danger' }),
)
export const trackScript: Record<string, string> = localized(() => L().trackScript)

const CJK = /[\u3400-\u9fff]/
/** Human text for a job's current phase in the active language. Phase codes map to dictionary labels; the server's
 * progress message (zh-TW) is shown as-is in Chinese, and in English only its numeric part ("3/40", "42%") is kept. */
export function jobPhaseText(job: Pick<Job, 'phase' | 'message'>): string {
  const phase = job.phase ?? ''
  const label = (L().phase as Record<string, string>)[phase.split(':')[0]]
  const msg = job.message ?? ''
  if (getLang() === 'zh-TW') return msg || label || phase
  const extra = !msg ? '' : !CJK.test(msg) ? msg : (msg.match(/\d+(?:\.\d+)?\s*%|\d+\s*\/\s*\d+/)?.[0] ?? '')
  if (label) return extra && extra !== label ? `${label} · ${extra}` : label
  return CJK.test(phase) ? extra : phase || extra
}

const PIPELINE: JobKind[] = ['media.download', 'media.normalize', 'media.quality', 'media.diarize', 'media.segment', 'media.embed', 'media.profile', 'media.fingerprint']
/** Media pipeline steps in order, with short labels in the active language. `ocr`: include the hard-subtitle OCR
 * step (runs between diarize and segment only for media with an OCR video). */
export const pipelineSteps = (o?: { ocr?: boolean }): { kind: JobKind; short: string }[] =>
  (o?.ocr ? PIPELINE.flatMap((k) => (k === 'media.diarize' ? [k, 'media.ocr' as JobKind] : [k])) : PIPELINE).map((kind) => ({
    kind,
    short: L().pipelineShort[kind as keyof ReturnType<typeof L>['pipelineShort']],
  }))

export const queueLabel: Record<string, string> = localized(() => L().queue)

export const reviewStatus: Record<ReviewStatus, { label: string; tone: Tone }> = localized(() =>
  withTone(L().reviewStatus, { unchecked: 'neutral', passed: 'success', needs_fix: 'warning', excluded: 'danger' }),
)

export const speakerState: Record<SpeakerState, string> = localized(() => L().speakerState)
export const textSource: Record<string, string> = localized(() => L().textSource)
export const flagLabel: Record<string, string> = localized(() => L().flag)
export const sexLabel: Record<string, string> = localized(() => L().sex)
export const emotionLabel: Record<string, string> = localized(() => L().emotion)
export const voiceQualityLabel: Record<string, string> = localized(() => L().voiceQuality)
export const speechFlowLabel: Record<string, string> = localized(() => L().speechFlow)

export const accentBroadLabel: Record<string, string> = localized(() => L().accentBroad)

export const ageGroupOrder = ['<20', '20-29', '30-39', '40-49', '50-59', '60+']
export const ageGroupLabel = (g: string) =>
  g === 'unknown'
    ? L().ageUnknown
    : g === '<20'
      ? L().ageUnder20
      : g === '60+'
        ? L().age60plus
        : ((L().ageWord as Record<string, string>)[g.toLowerCase()] ?? L().ageRange(g))

/** Vox-Profile emits capitalised / noun labels (Anger, Happiness, Fluent); the UI maps use lower-case adjectives. */
const TRAIT_ALIAS: Record<string, string> = { anger: 'angry', happiness: 'happy', sadness: 'sad' }
/** Label of a Vox-Profile trait value in the active language, tolerant of case and the model's label names. */
export function traitLabel(map: Record<string, string>, key: string): string {
  const k = key.toLowerCase()
  return map[key] ?? map[k] ?? map[TRAIT_ALIAS[k] ?? ''] ?? key
}

export const langLabel: Record<string, string> = localized(() => L().lang)
export const lang = (code: string) => langLabel[code] ?? langLabel[code.split('-')[0]] ?? code

/** Caption-language preference, same order as the backend picks tracks: Traditional / Taiwan Chinese first,
 * then Taiwanese and Hakka (downloaded alongside), then Simplified (converted to Traditional), then the rest. */
export function langRank(code: string): number {
  const c = code.toLowerCase()
  if (c === 'zh-tw') return 0
  if (c === 'zh-hant' || c.startsWith('zh-hant-')) return 1
  if (c === 'zh-hk' || c === 'zh-mo') return 2
  if (c === 'zh' || c === 'cmn') return 3
  if (c === 'nan' || c.startsWith('nan-')) return 4
  if (c === 'hak' || c.startsWith('hak-')) return 5
  if (c.startsWith('zh')) return 6
  return 10
}
export const byLangPreference = (a: string, b: string) => langRank(a) - langRank(b) || a.localeCompare(b)
/** Languages to pre-select for download: Chinese (any script) plus Taiwanese/Hakka. */
export const isPreferredCaptionLang = (code: string) => langRank(code) < 10

export const repeatPosition: Record<string, string> = localized(() => L().repeatPosition)
export const mediaKind: Record<string, string> = localized(() => L().mediaKind)
export const licenseLabel: Record<string, string> = localized(() => L().license)
