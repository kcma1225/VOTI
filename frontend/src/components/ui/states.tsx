import { useT } from '@/i18n'
import { errMsg } from '@/api/queries'
import type { ReactNode } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { cn } from '@/lib/cn'
import { ApiError } from '@/api/client'
import { Button } from './button'
import { Skeleton } from './primitives'

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  compact,
}: {
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  compact?: boolean
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center text-center', compact ? 'px-4 py-8' : 'px-6 py-14', className)}>
      {icon && (
        <div className="mb-3 grid size-11 place-items-center rounded-xl border border-border bg-surface-2 text-fg-3 [&_svg]:size-5">
          {icon}
        </div>
      )}
      <div className="text-[15px] font-semibold text-fg">{title}</div>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-5 text-fg-3 text-balance">{description}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const t = useT()
  const msg = error instanceof ApiError || (error instanceof Error && error.message) ? errMsg(error) : t('common.unexpectedError')
  const code = error instanceof ApiError ? error.code : null
  return (
    <div className={cn('flex flex-col items-center justify-center px-6 py-12 text-center', className)}>
      <div className="mb-3 grid size-11 place-items-center rounded-xl bg-danger-soft text-danger-fg">
        <AlertTriangle className="size-5" />
      </div>
      <div className="text-[15px] font-semibold">{t('common.loadError')}</div>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-fg-3">{msg}</p>
      {code && <code className="mt-1 text-[11px] text-fg-3">{code}</code>}
      {onRetry && (
        <Button className="mt-4" size="sm" onClick={onRetry}>
          <RotateCw /> {t('common.retry')}
        </Button>
      )}
    </div>
  )
}

export function ListSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('divide-y divide-border', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3">
          <Skeleton className="size-10 shrink-0 rounded-lg" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3.5" style={{ width: `${55 + ((i * 17) % 35)}%` }} />
            <Skeleton className="h-3 w-1/3" />
          </div>
          <Skeleton className="hidden h-6 w-16 rounded-full sm:block" />
        </div>
      ))}
    </div>
  )
}

export function CardsSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn('grid gap-3 sm:grid-cols-2 xl:grid-cols-4', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-xl border border-border bg-surface p-4">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="mt-3 h-7 w-28" />
          <Skeleton className="mt-2 h-3 w-16" />
        </div>
      ))}
    </div>
  )
}
