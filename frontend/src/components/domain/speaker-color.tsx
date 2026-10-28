/* Speaker colours. Within one media, a diarized speaker's colour = its index in the media's speaker list
 * (GET /media/{id}/speakers, most data first — the same order as the timeline speaker lane), mapped onto the
 * categorical chart palette (--series-1…8). Rows from many media use the same rule per media; until a media's list is
 * loaded (or for an older API) the label number is used (SPEAKER_00 → 0, SPEAKER_01 → 1, …). */
import { useMemo, type CSSProperties } from 'react'
import { useQueries } from '@tanstack/react-query'
import { api, ApiError } from '@/api/client'
import { qk, useMediaSpeakers } from '@/api/queries'
import type { MediaSpeaker, Timeline } from '@/api/types'
import { cn } from '@/lib/cn'
import { t } from '@/i18n'

export const SPEAKER_COLORS = 8

/** CSS colour of a speaker index (swatches, dots, lanes). */
export const speakerColor = (i: number) => `var(--series-${(((i % SPEAKER_COLORS) + SPEAKER_COLORS) % SPEAKER_COLORS) + 1})`

/** Custom properties consumed by .hl-current / .hl-sentence / .hl-seek / .hl-row-active (index.css). */
export function speakerVars(i: number | null | undefined): CSSProperties | undefined {
  if (i == null || i < 0) return undefined
  const k = (i % SPEAKER_COLORS) + 1
  return {
    '--spk': `var(--series-${k})`,
    '--spk-pill': `var(--spk-pill-${k})`,
    '--spk-on': `var(--spk-on-${k})`,
    '--spk-soft': `color-mix(in oklab, var(--series-${k}) var(--spk-soft-mix), transparent)`,
    '--spk-soft-fg': 'var(--fg)',
  } as CSSProperties
}

/** Label-order fallback: SPEAKER_07 → 7. */
export function speakerIndexFromLabel(label: string | null | undefined): number | null {
  if (!label) return null
  const m = /(\d+)\s*$/.exec(label)
  return m ? Number(m[1]) : null
}

/** Speaker order of a timeline, most data first (= the API's list): usable (not invalid) segment seconds, then
 * diarized seconds, then label. */
export function timelineSpeakers(tl: {
  diarization: Pick<Timeline['diarization'][number], 'speaker' | 'start' | 'end'>[]
  segments: Pick<Timeline['segments'][number], 'local_speaker' | 'validity' | 'start_sec' | 'end_sec'>[]
}): string[] {
  const diar = new Map<string, number>()
  const usable = new Map<string, number>()
  for (const d of tl.diarization) diar.set(d.speaker, (diar.get(d.speaker) ?? 0) + (d.end - d.start))
  for (const s of tl.segments) {
    if (!s.local_speaker) continue
    const v = s.validity === 'invalid' ? 0 : s.end_sec - s.start_sec
    usable.set(s.local_speaker, (usable.get(s.local_speaker) ?? 0) + v)
  }
  const r = (x: number | undefined) => Math.round((x ?? 0) * 1000)
  return [...new Set([...diar.keys(), ...usable.keys()])].sort(
    (a, b) => r(usable.get(b)) - r(usable.get(a)) || r(diar.get(b)) - r(diar.get(a)) || (a < b ? -1 : a > b ? 1 : 0),
  )
}

/** Friendly speaker name within one media: 語者 1 / Speaker 1 (index + 1). */
export const localSpeakerName = (i: number) => t('people.mediaSpeakers.name', i + 1)

function indexer(list: MediaSpeaker[] | undefined) {
  const map = new Map<string, number>()
  list?.forEach((s) => map.set(s.local_speaker, s.color_index))
  return (label: string | null | undefined): number | null => {
    if (!label) return null
    return map.get(label) ?? speakerIndexFromLabel(label)
  }
}

/** `label → colour index` for one media. */
export function useSpeakerIndex(mediaId: string | undefined) {
  const q = useMediaSpeakers(mediaId ?? '', !!mediaId)
  return useMemo(() => indexer(q.data), [q.data])
}

/** `(mediaId, label) → colour index` for rows from many media (review list). */
export function useSpeakerIndexMany(mediaIds: string[]) {
  const ids = [...new Set(mediaIds)].sort()
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: qk.mediaSpeakers(id),
      queryFn: async () => {
        try {
          return await api.mediaSpeakers(id)
        } catch (e) {
          if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return [] as MediaSpeaker[]
          throw e
        }
      },
      staleTime: 60_000,
    })),
  })
  const stamp = results.map((r) => r.dataUpdatedAt).join(',')
  return useMemo(() => {
    const per = new Map(ids.map((id, i) => [id, indexer(results[i]?.data)]))
    return (mediaId: string, label: string | null | undefined) => (per.get(mediaId) ?? indexer(undefined))(label)
  }, [ids.join(','), stamp]) // eslint-disable-line react-hooks/exhaustive-deps
}

/** Small colour dot (+ optional text) identifying a speaker on a row. */
export function SpeakerDot({ index, label, title, className }: { index: number | null; label?: string | null; title?: string; className?: string }) {
  if (index == null) return null
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1', className)} title={title}>
      <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: speakerColor(index) }} />
      {label && <span className="truncate">{label}</span>}
    </span>
  )
}
