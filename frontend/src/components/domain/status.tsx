import { t } from '@/i18n'
import { AlertCircle, Ban, CheckCircle2, CircleDashed, Clock, Loader2, PauseCircle } from 'lucide-react'
import type { CaptionCheck, JobStatus, MediaStatus, ReviewStatus } from '@/api/types'
import { jobStatus, mediaStatus, reviewStatus, type Tone } from '@/lib/labels'
import { Badge, Tag } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/overlay'
import { cn } from '@/lib/cn'
import { byLangPreference, lang } from '@/lib/labels'
import { Captions, CaptionsOff, Bot } from 'lucide-react'

/** `compact` (tables, card lists): the members-only state reads 「會員限定」/"Members only" with the full explanation in
 * a tooltip. Every status badge truncates inside its pill (see Badge) and carries its full text as a title. */
export function MediaStatusBadge({
  status,
  stage,
  failureCode,
  compact,
  className,
}: {
  status: MediaStatus
  stage?: string | null
  failureCode?: string | null
  compact?: boolean
  className?: string
}) {
  if (failureCode === 'members_only')
    return (
      <Tooltip content={`${t('labels.membersOnly')} — ${t('labels.membersOnlyHint')}`}>
        <Badge tone="warning" className={className} aria-label={`${t('labels.membersOnly')}: ${t('labels.membersOnlyHint')}`} data-status="members_only">
          <Ban className="size-3" /> {compact ? t('labels.membersOnlyShort') : t('labels.membersOnly')}
        </Badge>
      </Tooltip>
    )
  if (failureCode === 'empty_captions')
    return (
      <Tooltip content={`${t('labels.emptyCaptions')} — ${t('labels.emptyCaptionsHint')}`}>
        <Badge tone="warning" className={className} aria-label={`${t('labels.emptyCaptions')}: ${t('labels.emptyCaptionsHint')}`} data-status="empty_captions">
          <CaptionsOff className="size-3" /> {compact ? t('labels.emptyCaptionsShort') : t('labels.emptyCaptions')}
        </Badge>
      </Tooltip>
    )
  if (failureCode === 'no_subtitles' && status === 'skipped')
    return (
      <Tooltip content={`${t('labels.noSubtitles')} — ${t('labels.noSubtitlesHint')}`}>
        <Badge tone="warning" className={className} aria-label={`${t('labels.noSubtitles')}: ${t('labels.noSubtitlesHint')}`} data-status="no_subtitles">
          <CaptionsOff className="size-3" /> {compact ? t('labels.noSubtitlesShort') : t('labels.noSubtitles')}
        </Badge>
      </Tooltip>
    )
  const s = mediaStatus[status] ?? { label: status, tone: 'neutral' as Tone }
  const active = status === 'downloading' || status === 'processing'
  return (
    <Badge tone={s.tone} dot pulse={active} className={className} title={stage ? `${s.label} · ${stage}` : s.label} data-status={status}>
      {s.label}
    </Badge>
  )
}

export function JobStatusBadge({ status, className }: { status: JobStatus; className?: string }) {
  const s = jobStatus[status]
  return (
    <Badge tone={s.tone} dot pulse={status === 'running'} className={className} title={s.label} data-status={status}>
      {s.label}
    </Badge>
  )
}

export function JobStatusIcon({ status, className }: { status: JobStatus; className?: string }) {
  const cls = cn('size-4 shrink-0', className)
  switch (status) {
    case 'running':
      return <Loader2 className={cn(cls, 'animate-spin text-primary')} />
    case 'queued':
      return <Clock className={cn(cls, 'text-fg-3')} />
    case 'completed':
      return <CheckCircle2 className={cn(cls, 'text-success')} />
    case 'failed':
      return <AlertCircle className={cn(cls, 'text-danger')} />
    case 'blocked':
    case 'interrupted':
      return <PauseCircle className={cn(cls, 'text-warning')} />
    case 'cancelled':
      return <Ban className={cn(cls, 'text-fg-3')} />
    default:
      return <CircleDashed className={cls} />
  }
}

export function ReviewBadge({ status, className }: { status: ReviewStatus; className?: string }) {
  const s = reviewStatus[status]
  return (
    <Badge tone={s.tone} dot className={className} title={s.label} data-status={status}>
      {s.label}
    </Badge>
  )
}

/** Native (uploader) captions vs YouTube auto captions — the key facet of the product. */
export function CaptionBadges({
  manual,
  auto,
  manualLangs,
  autoLangs,
  compact,
  empty,
  check,
}: {
  manual: boolean | null
  auto: boolean | null
  manualLangs: string[]
  autoLangs: string[]
  compact?: boolean
  /** MediaSummary.captions_empty: the uploader tracks YouTube lists have no content */
  empty?: boolean
  check?: CaptionCheck | null
}) {
  manualLangs = [...manualLangs].sort(byLangPreference)
  autoLangs = [...autoLangs].sort(byLangPreference)
  if (manual === null && auto === null && !empty)
    return (
      <Tooltip content={t('process.status.captionsUnknownTip')}>
        <span className="text-xs text-fg-3">{t('process.status.unknown')}</span>
      </Tooltip>
    )
  const emptyTracks = Object.entries(check?.tracks ?? {})
    .filter(([, v]) => v.status === 'empty')
    .map(([l, v]) => t('process.status.captionsEmptyTrack', lang(l), v.speech_cues ?? 0))
  const emptyBadge = empty ? (
    <Tooltip content={t('process.status.captionsEmptyTip', (emptyTracks.length ? emptyTracks : manualLangs.map(lang)).join(t('common.listSep')) || '—')}>
      <Tag className="max-w-full border-warning/40 bg-warning-soft text-warning-fg" data-captions-empty>
        <CaptionsOff className="size-3 shrink-0" />
        <span className="truncate">{t('process.status.captionsEmpty')}</span>
      </Tag>
    </Tooltip>
  ) : null
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {emptyBadge}
      {manual && !empty ? (
        <Tooltip content={manualLangs.length ? t('process.status.manualTip', manualLangs.map(lang).join(t('common.listSep'))) : t('ytapi.langsUnknownTip')}>
          <Tag className="border-success/30 bg-success-soft text-success-fg">
            <Captions className="size-3" />
            {compact ? t('process.status.manualShort') : manualLangs.slice(0, 2).join(' · ') || t('process.status.manualShort')}
          </Tag>
        </Tooltip>
      ) : null}
      {auto ? (
        <Tooltip content={t('process.status.autoTip', autoLangs.map(lang).join(t('common.listSep')) || '—')}>
          <Tag className="text-fg-3">
            <Bot className="size-3" />
            {t('process.status.autoShort')}
          </Tag>
        </Tooltip>
      ) : null}
      {!manual && !auto && !empty && <span className="text-xs text-fg-3">{t('process.status.noCaptions')}</span>}
    </div>
  )
}
