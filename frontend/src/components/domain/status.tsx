import { AlertCircle, Ban, CheckCircle2, CircleDashed, Clock, Loader2, PauseCircle } from 'lucide-react'
import type { JobStatus, MediaStatus, ReviewStatus } from '@/api/types'
import { jobStatus, mediaStatus, reviewStatus, type Tone } from '@/lib/labels'
import { Badge, Tag } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/overlay'
import { cn } from '@/lib/cn'
import { lang } from '@/lib/labels'
import { Captions, Bot } from 'lucide-react'

export function MediaStatusBadge({ status, stage, className }: { status: MediaStatus; stage?: string | null; className?: string }) {
  const s = mediaStatus[status] ?? { label: status, tone: 'neutral' as Tone }
  const active = status === 'downloading' || status === 'processing'
  return (
    <Badge tone={s.tone} dot pulse={active} className={className} title={stage ?? undefined}>
      {s.label}
    </Badge>
  )
}

export function JobStatusBadge({ status, className }: { status: JobStatus; className?: string }) {
  const s = jobStatus[status]
  return (
    <Badge tone={s.tone} dot pulse={status === 'running'} className={className}>
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
    <Badge tone={s.tone} dot className={className}>
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
}: {
  manual: boolean | null
  auto: boolean | null
  manualLangs: string[]
  autoLangs: string[]
  compact?: boolean
}) {
  if (manual === null && auto === null)
    return (
      <Tooltip content="尚未取得影片詳細資訊，字幕狀態未知">
        <span className="text-xs text-fg-3">未知</span>
      </Tooltip>
    )
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {manual ? (
        <Tooltip content={`原生字幕：${manualLangs.map(lang).join('、') || '—'}`}>
          <Tag className="border-success/30 bg-success-soft text-success-fg">
            <Captions className="size-3" />
            {compact ? '原生' : manualLangs.slice(0, 2).join(' · ') || '原生'}
          </Tag>
        </Tooltip>
      ) : null}
      {auto ? (
        <Tooltip content={`YouTube 自動字幕：${autoLangs.map(lang).join('、') || '—'}`}>
          <Tag className="text-fg-3">
            <Bot className="size-3" />
            自動
          </Tag>
        </Tooltip>
      ) : null}
      {!manual && !auto && <span className="text-xs text-fg-3">無字幕</span>}
    </div>
  )
}
