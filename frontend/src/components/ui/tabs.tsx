import { intlLocale, t } from '@/i18n'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router'
import { cn } from '@/lib/cn'

export interface TabItem {
  to: string
  label: ReactNode
  icon?: ReactNode
  count?: number | null
  end?: boolean
}

/** Route-driven underline tabs. Horizontally scrollable on narrow screens (scroll is inside the bar, never the page). */
export function NavTabs({ items, className }: { items: TabItem[]; className?: string }) {
  return (
    <div className={cn('relative -mx-4 border-b border-border sm:mx-0', className)}>
      <nav className="flex gap-1 overflow-x-auto px-4 no-scrollbar sm:px-0" aria-label={t('common.tabs')}>
        {items.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            replace
            className={({ isActive }) =>
              cn(
                'relative inline-flex h-11 shrink-0 items-center gap-1.5 whitespace-nowrap px-3 text-sm font-medium transition-colors [&_svg]:size-4',
                isActive
                  ? 'text-fg after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:bg-primary'
                  : 'text-fg-3 hover:text-fg',
              )
            }
          >
            {t.icon}
            {t.label}
            {t.count != null && (
              <span className="rounded-full bg-surface-2 px-1.5 text-[11px] leading-[18px] tabular text-fg-3">
                {t.count.toLocaleString(intlLocale())}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

/** State-driven pill tabs. */
export function PillTabs<V extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: V
  onChange: (v: V) => void
  items: { value: V; label: ReactNode; count?: number }[]
  className?: string
}) {
  return (
    <div className={cn('flex gap-1 overflow-x-auto no-scrollbar', className)} role="tablist">
      {items.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            'inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-[13px] font-medium transition-colors sm:h-8',
            value === t.value ? 'bg-surface-3 text-fg' : 'text-fg-3 hover:bg-surface-2 hover:text-fg',
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="tabular text-xs text-fg-3">{t.count.toLocaleString(intlLocale())}</span>}
        </button>
      ))}
    </div>
  )
}
