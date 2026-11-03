import { getLang, useT } from '@/i18n'
import { Link } from 'react-router'
import { Hourglass, RotateCcw, RotateCw, X } from 'lucide-react'
import type { Job } from '@/api/types'
import { useJobAction } from '@/api/queries'
import { jobKind, jobPhaseText, queueLabel } from '@/lib/labels'
import { fmtEta, fmtRelative } from '@/lib/format'
import { fmtCountdown, useNow } from '@/hooks/useNow'
import { Button } from '@/components/ui/button'
import { Badge, Progress } from '@/components/ui/primitives'
import { JobStatusIcon } from './status'
import { cn } from '@/lib/cn'

/** Automatic-retry / recovery state of a queued job (see ARCHITECTURE "Job recovery"). */
export function jobRecovery(job: Job) {
  if (job.status !== 'queued') return null
  if (job.phase === 'retry_wait') return 'retry' as const
  if (job.phase === 'recovered' || job.phase === 'requeued') return job.phase
  return null
}

/** Max attempts: from the API when present, else parsed from the retry message ("第 2/4 次"). */
function maxAttempts(job: Job): number | null {
  if (job.max_attempts) return job.max_attempts
  const m = job.message?.match(/(\d+)\s*\/\s*(\d+)/)
  return m ? Number(m[2]) : null
}

export function RetryCountdown({ job, className }: { job: Job; className?: string }) {
  const t = useT()
  const due = job.run_after ? new Date(job.run_after).getTime() : null
  const now = useNow(1000, !!due)
  const left = due ? (due - now) / 1000 : 0
  return (
    <span className={cn('inline-flex items-center gap-1 font-medium tabular', className)}>
      <Hourglass className="size-3 shrink-0" />
      {due && left > 0.5 ? t('process.jobRow.retryIn', fmtCountdown(left)) : t('process.jobRow.retryNow')}
    </span>
  )
}

export function JobRow({ job, className, showActions = true, hideSubject }: { job: Job; className?: string; showActions?: boolean; hideSubject?: boolean }) {
  const t = useT()
  const action = useJobAction()
  const canRetry = ['failed', 'blocked', 'interrupted', 'cancelled'].includes(job.status)
  const canCancel = job.status === 'queued' || job.status === 'running'
  const subject = hideSubject ? null : (job.media_title ?? job.channel_title ?? null)
  const recovery = jobRecovery(job)
  const zh = getLang() === 'zh-TW'
  return (
    <div className={cn('flex min-w-0 items-start gap-3 px-4 py-3', className)}>
      <JobStatusIcon status={job.status} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline justify-between gap-2">
          <span className="truncate text-[13px] font-medium text-fg">{jobKind[job.kind] ?? job.kind}</span>
          <span className="shrink-0 text-xs text-fg-3 tabular">
            {job.status === 'running' && job.eta_sec ? t('process.jobRow.remaining', fmtEta(job.eta_sec)) : fmtRelative(job.finished_at ?? job.started_at ?? job.created_at)}
          </span>
        </div>
        {subject &&
          (job.media_id ? (
            <Link to={`/media/${job.media_id}`} className="mt-0.5 block truncate text-xs text-fg-2 hover:text-primary hover:underline">
              {subject}
            </Link>
          ) : job.channel_id ? (
            <Link to={`/channels/${job.channel_id}`} className="mt-0.5 block truncate text-xs text-fg-2 hover:text-primary hover:underline">
              {subject}
            </Link>
          ) : (
            <div className="mt-0.5 truncate text-xs text-fg-2">{subject}</div>
          ))}
        {job.status === 'running' && (
          <div className="mt-2 flex items-center gap-2">
            <Progress value={job.progress} indeterminate={!job.progress} className="flex-1" />
            <span className="w-9 shrink-0 text-right text-[11px] tabular text-fg-3">{Math.round(job.progress * 100)}%</span>
          </div>
        )}
        {recovery === 'retry' && (
          <div className="mt-1.5 rounded-md bg-warning-soft px-2 py-1.5 text-[11px] leading-4 text-warning-fg">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <RetryCountdown job={job} />
              <span className="opacity-80">{t('process.jobRow.retryAttempt', job.attempts + 1, maxAttempts(job))}</span>
            </div>
            {(job.error_code || job.error_message) && (
              <div className="mt-0.5 line-clamp-2 break-cjk opacity-90">
                {job.error_code && <span className="font-mono">{t('process.jobRow.errorCode', job.error_code)}</span>}
                {job.error_message ?? t('process.jobRow.retryDesc', job.error_code ?? '')}
              </div>
            )}
          </div>
        )}
        {(recovery === 'recovered' || recovery === 'requeued') && (
          <div className="mt-1.5 min-w-0 space-y-1">
            <Badge tone="info">
              <RotateCcw className="size-3" /> {recovery === 'recovered' ? t('process.jobRow.recovered') : t('process.jobRow.requeued')}
            </Badge>
            <p className="min-w-0 text-[11px] leading-4 text-fg-3 break-cjk">
              {zh && job.message ? job.message : recovery === 'recovered' ? t('process.jobRow.recoveredDesc') : t('process.jobRow.requeuedDesc')}
            </p>
          </div>
        )}
        {job.error_message && (job.status === 'failed' || job.status === 'blocked' || job.status === 'interrupted') && (
          <div className="mt-1.5 line-clamp-2 rounded-md bg-danger-soft px-2 py-1 text-[11px] leading-4 text-danger-fg break-cjk">
            {job.error_code && <span className="font-mono">{t('process.jobRow.errorCode', job.error_code)}</span>}
            {job.status === 'interrupted' && !zh ? t('process.jobRow.interruptedDesc') : job.error_message}
          </div>
        )}
        <div className="mt-1 truncate text-[11px] text-fg-3">
          {job.status === 'running' && (job.message || job.phase) ? `${jobPhaseText(job)} · ` : ''}
          {t('process.jobRow.queue', queueLabel[job.queue] ?? job.queue)}
          {job.attempts > 1 && !recovery ? ` · ${t('process.jobRow.attempt', job.attempts)}` : ''}
        </div>
      </div>
      {showActions && (canRetry || canCancel) && (
        <Button
          size="xs"
          variant={canRetry ? 'secondary' : 'ghost'}
          className="shrink-0"
          loading={action.isPending}
          onClick={() => action.mutate({ id: job.id, action: canRetry ? 'retry' : 'cancel' })}
        >
          {canRetry ? <RotateCw /> : <X />}
          {canRetry ? t('common.retry') : t('common.cancel')}
        </Button>
      )}
    </div>
  )
}
