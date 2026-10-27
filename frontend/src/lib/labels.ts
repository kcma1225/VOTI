import { localized, msgs } from '@/i18n'
import type { JobKind, JobStatus, MediaStatus, ReviewStatus, SpeakerState } from '@/api/types'

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

const PIPELINE: JobKind[] = ['media.download', 'media.normalize', 'media.quality', 'media.diarize', 'media.segment', 'media.embed', 'media.profile', 'media.fingerprint']
/** Media pipeline steps in order, with short labels in the active language. */
export const pipelineSteps = (): { kind: JobKind; short: string }[] =>
  PIPELINE.map((kind) => ({ kind, short: L().pipelineShort[kind as keyof ReturnType<typeof L>['pipelineShort']] }))

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

export const ageGroupOrder = ['<20', '20-29', '30-39', '40-49', '50-59', '60+']
export const ageGroupLabel = (g: string) =>
  g === 'unknown' ? L().ageUnknown : g === '<20' ? L().ageUnder20 : g === '60+' ? L().age60plus : L().ageRange(g)

export const langLabel: Record<string, string> = localized(() => L().lang)
export const lang = (code: string) => langLabel[code] ?? code

export const repeatPosition: Record<string, string> = localized(() => L().repeatPosition)
export const mediaKind: Record<string, string> = localized(() => L().mediaKind)
export const licenseLabel: Record<string, string> = localized(() => L().license)
