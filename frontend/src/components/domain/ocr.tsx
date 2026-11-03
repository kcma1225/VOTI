/* Hard-subtitle OCR: text-source badge, event crop image, status badge, reason labels, event ↔ segment matching. */
import { useState } from 'react'
import { ImageOff, Loader2, ScanText } from 'lucide-react'
import { msgs, t } from '@/i18n'
import type { OcrEvent, OcrResult, OcrSummary, SegmentDetail, SegmentSummary } from '@/api/types'
import { cn } from '@/lib/cn'
import { fmtTimestamp } from '@/lib/format'
import { useMediaOcr } from '@/api/queries'
import { Badge, Tag } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/overlay'

export const isOcrSource = (s: Pick<SegmentSummary, 'text_source'> | null | undefined) => s?.text_source === 'ocr_caption'

/** OCR rejection reason → label in the active language (unknown codes as-is). */
export const ocrReason = (r: string | null | undefined): string => (r ? ((msgs().ocr.reason as Record<string, string>)[r] ?? r) : '')
export const ocrReasonTip = (r: string | null | undefined): string => (r ? ((msgs().ocr.reasonTip as Record<string, string>)[r] ?? '') : '')

/** 「畫面字幕（OCR）」 text-source marker. `tag`: the small rectangular metadata style (rows); else a pill badge.
 * `short`: 「畫面字幕」/"On-screen subs" for tight spots (the tooltip carries the full explanation). */
export function OcrSourceBadge({ tag, short, className }: { tag?: boolean; short?: boolean; className?: string }) {
  const label = short ? t('ocr.sourceShort') : t('ocr.source')
  return (
    <Tooltip content={<span className="block max-w-72 text-xs leading-5">{t('ocr.sourceTip')}</span>}>
      {tag ? (
        <Tag className={cn('border-ocr/30 bg-ocr-soft text-ocr-fg', className)} data-text-source="ocr_caption">
          <ScanText className="size-3 shrink-0" />
          <span className="truncate">{label}</span>
        </Tag>
      ) : (
        <Badge className={cn('bg-ocr-soft text-ocr-fg', className)} data-text-source="ocr_caption">
          <ScanText className="size-3" />
          {label}
        </Badge>
      )}
    </Tooltip>
  )
}

/** 「OCR 未驗證」: an OCR segment that alignment could not confirm (validation.note = ocr_unverified). */
export function OcrUnverifiedTag({ className }: { className?: string }) {
  return (
    <Tooltip content={<span className="block max-w-72 text-xs leading-5">{t('ocr.unverifiedTip')}</span>}>
      <Tag className={cn('border-warning/30 text-warning-fg', className)}>{t('ocr.unverified')}</Tag>
    </Tooltip>
  )
}
/** validity unknown on an OCR segment = not confirmed by alignment (datasets only take valid OCR segments). */
export const isOcrUnverified = (s: Pick<SegmentSummary, 'text_source' | 'validity'>) => isOcrSource(s) && s.validity === 'unknown'

/** OCR status (none / pending / running / done / no_subtitles / failed) as a badge. */
export function OcrStatusBadge({ status, active, className }: { status: OcrResult['status'] | OcrSummary['status'] | undefined; active?: boolean; className?: string }) {
  if (active)
    return (
      <Badge tone="primary" className={className}>
        <Loader2 className="size-3 animate-spin" /> {t('ocr.status.running')}
      </Badge>
    )
  const st = status ?? 'none'
  const tone = st === 'done' ? 'success' : st === 'failed' ? 'danger' : st === 'no_subtitles' ? 'warning' : st === 'pending' ? 'info' : 'neutral'
  return (
    <Badge tone={tone} dot className={className} data-ocr-status={st}>
      {(msgs().ocr.status as Record<string, string>)[st] ?? st}
    </Badge>
  )
}

/** One event's crop (≤ 96 px high webp): subtitle text on its video background. Lazy; a quiet placeholder on error. */
export function OcrCrop({ src, alt, className, imgClassName }: { src: string | null; alt: string; className?: string; imgClassName?: string }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className={cn('flex min-w-0 items-center justify-center overflow-hidden rounded-md bg-[#111418] ring-1 ring-border', className)}>
      {src && !failed ? (
        <img src={src} alt={alt} loading="lazy" decoding="async" onError={() => setFailed(true)} className={cn('block max-h-full max-w-full object-contain', imgClassName)} />
      ) : (
        <span className="flex items-center gap-1.5 px-2 text-[11px] text-white/55">
          <ImageOff className="size-3.5 shrink-0" /> {t('ocr.event.noCrop')}
        </span>
      )}
    </span>
  )
}

/** Time window an event covers for playback / matching: the speech-snapped span when paired, else the event. */
export const eventSpan = (e: OcrEvent): [number, number] => [e.paired_start ?? e.start, e.paired_end ?? e.end]

/** OCR events a segment was built from: its cues first (segment.cue_ids), else paired events overlapping it. */
export function eventsForSegment(events: OcrEvent[] | undefined, s: Pick<SegmentDetail, 'start_sec' | 'end_sec'> & { cue_ids?: string[] }): OcrEvent[] {
  if (!events?.length) return []
  const cues = new Set(s.cue_ids ?? [])
  const byCue = cues.size ? events.filter((e) => e.cue_id && cues.has(e.cue_id)) : []
  if (byCue.length) return byCue
  return events.filter((e) => {
    const [a, b] = eventSpan(e)
    const ov = Math.min(b, s.end_sec) - Math.max(a, s.start_sec)
    return e.status === 'paired' && ov > Math.min(0.3, (b - a) / 2)
  })
}

/** Reviewer: the crop(s) of the OCR event(s) an ocr_caption segment was built from, to compare with the text. */
export function OcrCropsPanel({ s, className }: { s: Pick<SegmentDetail, 'media_id' | 'start_sec' | 'end_sec' | 'cue_ids' | 'original_text'>; className?: string }) {
  const q = useMediaOcr(s.media_id)
  const events = eventsForSegment(q.data?.events, s)
  const MAX = 4
  return (
    <div data-shot="review-ocr-crops" className={cn('rounded-lg border border-ocr/25 bg-ocr-soft/40 p-2.5 sm:p-3', className)}>
      <div className="mb-2 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ocr-fg">
          <ScanText className="size-3.5" /> {t('ocr.review.cropsTitle')}
        </span>
        <span className="text-[11px] text-fg-3 break-cjk">{t('ocr.review.cropsDesc')}</span>
      </div>
      {!q.data ? (
        q.isError ? (
          <p className="text-[11px] text-fg-3">{t('ocr.review.none')}</p>
        ) : (
          <div className="h-12 animate-shimmer rounded-md bg-surface-3" aria-label={t('ocr.review.loading')} />
        )
      ) : !events.length ? (
        <p className="text-[11px] text-fg-3">{t('ocr.review.none')}</p>
      ) : (
        <ul className="space-y-2">
          {events.slice(0, MAX).map((e) => (
            <li key={e.idx} className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
              <OcrCrop src={e.crop_url} alt={t('ocr.event.cropAlt', e.raw_text)} className="h-12 w-full shrink-0 sm:w-[26rem] sm:max-w-[60%]" />
              <div className="min-w-0 flex-1 text-[11px] leading-4 text-fg-3">
                <div className="flex flex-wrap gap-x-2">
                  <span className="font-mono">
                    {fmtTimestamp(e.start)}–{fmtTimestamp(e.end)}
                  </span>
                  <span className={cn('tabular', e.conf < 0.85 && 'font-medium text-warning-fg')}>{t('ocr.event.conf', e.conf)}</span>
                  {e.s2t_converted && <span>{t('ocr.event.s2t')}</span>}
                </div>
                {e.raw_text !== e.text && (
                  <div className="mt-0.5 break-cjk">
                    {t('ocr.event.raw')}：{e.raw_text}
                  </div>
                )}
              </div>
            </li>
          ))}
          {events.length > MAX && <li className="text-[11px] text-fg-3">{t('ocr.review.more', events.length - MAX)}</li>}
        </ul>
      )}
    </div>
  )
}
