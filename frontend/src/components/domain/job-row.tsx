import { Link } from 'react-router'
import { RotateCw, X } from 'lucide-react'
import type { Job } from '@/api/types'
import { useJobAction } from '@/api/queries'
import { jobKind, queueLabel } from '@/lib/labels'
import { fmtEta, fmtRelative } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/primitives'
import { JobStatusIcon } from './status'
import { cn } from '@/lib/cn'

export function JobRow({ job, className, showActions = true, hideSubject }: { job: Job; className?: string; showActions?: boolean; hideSubject?: boolean }) {
  const action = useJobAction()
  const canRetry = ['failed', 'blocked', 'interrupted', 'cancelled'].includes(job.status)
  const canCancel = job.status === 'queued' || job.status === 'running'
  const subject = hideSubject ? null : (job.media_title ?? job.channel_title ?? null)
  return (
    <div className={cn('flex min-w-0 items-start gap-3 px-4 py-3', className)}>
      <JobStatusIcon status={job.status} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline justify-between gap-2">
          <span className="truncate text-[13px] font-medium text-fg">{jobKind[job.kind] ?? job.kind}</span>
          <span className="shrink-0 text-xs text-fg-3 tabular">
            {job.status === 'running' && job.eta_sec ? `剩 ${fmtEta(job.eta_sec)}` : fmtRelative(job.finished_at ?? job.started_at ?? job.created_at)}
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
        {job.error_message && (job.status === 'failed' || job.status === 'blocked' || job.status === 'interrupted') && (
          <div className="mt-1.5 line-clamp-2 rounded-md bg-danger-soft px-2 py-1 text-[11px] leading-4 text-danger-fg break-cjk">
            {job.error_code && <span className="font-mono">{job.error_code}：</span>}
            {job.error_message}
          </div>
        )}
        <div className="mt-1 truncate text-[11px] text-fg-3">
          {job.status === 'running' && (job.message || job.phase) ? `${job.message || job.phase} · ` : ''}
          {queueLabel[job.queue] ?? job.queue}佇列{job.attempts > 1 ? ` · 第 ${job.attempts} 次嘗試` : ''}
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
          {canRetry ? '重試' : '取消'}
        </Button>
      )}
    </div>
  )
}
