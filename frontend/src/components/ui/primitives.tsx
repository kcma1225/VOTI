import { useState, type HTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'
import type { Tone } from '@/lib/labels'

/* ───────────── Badge ───────────── */
const toneSoft: Record<Tone, string> = {
  neutral: 'bg-neutral-soft text-neutral-fg',
  primary: 'bg-primary-soft text-primary-soft-fg',
  info: 'bg-info-soft text-info-fg',
  success: 'bg-success-soft text-success-fg',
  warning: 'bg-warning-soft text-warning-fg',
  danger: 'bg-danger-soft text-danger-fg',
}
export const toneDot: Record<Tone, string> = {
  neutral: 'bg-fg-3',
  primary: 'bg-primary text-primary',
  info: 'bg-info text-info',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

export function Badge({
  tone = 'neutral',
  dot,
  pulse,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean; pulse?: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-[22px] max-w-full shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs font-medium leading-none',
        toneSoft[tone],
        className,
      )}
      {...props}
    >
      {dot && <span className={cn('size-1.5 shrink-0 rounded-full', toneDot[tone], pulse && 'animate-pulse-dot')} />}
      {children}
    </span>
  )
}

/** Small rectangular tag for metadata (caption language, flags). */
export function Tag({ className, children, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded border border-border bg-surface px-1.5 text-[11px] font-medium leading-none text-fg-2',
        className,
      )}
      {...props}
    >
      {children}
    </span>
  )
}

/* ───────────── Card ───────────── */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('min-w-0 rounded-xl border border-border bg-surface shadow-xs', className)} {...props} />
}
export function CardHeader({
  title,
  description,
  actions,
  icon,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  icon?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-2 px-4 pt-4 sm:px-5', className)}>
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        {icon && <div className="mt-0.5 shrink-0 text-fg-3 [&_svg]:size-4">{icon}</div>}
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold leading-6 text-fg">{title}</h3>
          {description && <p className="mt-0.5 text-[13px] leading-5 text-fg-3">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-4 sm:p-5', className)} {...props} />
}

/* ───────────── Skeleton ───────────── */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden className={cn('animate-shimmer rounded-md bg-surface-3', className)} {...props} />
}

/* ───────────── Kbd ───────────── */
export function Kbd({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded border border-border-strong bg-surface-2 px-1 font-mono text-[11px] font-medium leading-none text-fg-2 shadow-[inset_0_-1px_0_var(--border-strong)]',
        className,
      )}
    >
      {children}
    </kbd>
  )
}

/* ───────────── Progress ───────────── */
export function Progress({
  value,
  tone = 'primary',
  className,
  indeterminate,
}: {
  value?: number | null
  tone?: Tone
  className?: string
  indeterminate?: boolean
}) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100
  const bar: Record<Tone, string> = {
    neutral: 'bg-fg-3', primary: 'bg-primary', info: 'bg-info', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger',
  }
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      className={cn('relative h-1.5 w-full overflow-hidden rounded-full bg-surface-3', className)}
    >
      {indeterminate ? (
        <div className={cn('absolute inset-y-0 left-0 w-2/5 animate-progress-indeterminate rounded-full', bar[tone])} />
      ) : (
        <div className={cn('h-full rounded-full transition-[width] duration-500 ease-out', bar[tone])} style={{ width: `${pct}%` }} />
      )}
    </div>
  )
}

/* ───────────── Avatar / thumbnails ───────────── */
function hash(s: string) {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}
export function hueOf(s: string) {
  return hash(s) % 360
}

export function Avatar({
  name,
  src,
  size = 40,
  className,
  rounded = 'full',
}: {
  name: string
  src?: string | null
  size?: number
  className?: string
  rounded?: 'full' | 'lg'
}) {
  const [failed, setFailed] = useState(false)
  const hue = hueOf(name)
  const initial = Array.from(name.trim())[0] ?? '?'
  return (
    <div
      className={cn(
        'relative grid shrink-0 place-items-center overflow-hidden font-semibold text-white',
        rounded === 'full' ? 'rounded-full' : 'rounded-lg',
        className,
      )}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        background: `linear-gradient(135deg, hsl(${hue} 55% 52%), hsl(${(hue + 40) % 360} 60% 40%))`,
      }}
      aria-hidden
    >
      {src && !failed ? (
        <img src={src} alt="" className="size-full object-cover" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        initial
      )}
    </div>
  )
}

export function Thumb({
  src,
  seed,
  className,
  children,
}: {
  src?: string | null
  seed: string
  className?: string
  children?: ReactNode
}) {
  const [failed, setFailed] = useState(false)
  const hue = hueOf(seed)
  return (
    <div
      className={cn('relative shrink-0 overflow-hidden rounded-md bg-surface-3', className)}
      style={
        !src || failed
          ? { background: `linear-gradient(135deg, hsl(${hue} 35% 62%), hsl(${(hue + 50) % 360} 40% 38%))` }
          : undefined
      }
    >
      {src && !failed && (
        <img src={src} alt="" loading="lazy" className="absolute inset-0 size-full object-cover" onError={() => setFailed(true)} />
      )}
      {children}
    </div>
  )
}

/* ───────────── Stat tile ───────────── */
export function Stat({
  label,
  value,
  unit,
  hint,
  icon,
  className,
}: {
  label: ReactNode
  value: ReactNode
  unit?: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex items-center gap-1.5 text-[13px] text-fg-3 [&_svg]:size-3.5">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="truncate text-[22px] font-semibold leading-8 tracking-tight text-fg">{value}</span>
        {unit && <span className="shrink-0 text-[13px] text-fg-3">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 truncate text-xs text-fg-3">{hint}</div>}
    </div>
  )
}

/* ───────────── Section heading ───────────── */
export function SectionTitle({ children, actions, className }: { children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn('mb-3 flex items-center justify-between gap-3', className)}>
      <h2 className="text-[15px] font-semibold text-fg">{children}</h2>
      {actions}
    </div>
  )
}

export function Separator({ className, vertical }: { className?: string; vertical?: boolean }) {
  return <div className={cn(vertical ? 'w-px self-stretch bg-border' : 'h-px w-full bg-border', className)} />
}

/** Key/value list used in metadata panels. */
export function DescList({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn('grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-[13px]', className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="whitespace-nowrap text-fg-3">{k}</dt>
          <dd className="min-w-0 break-cjk text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
