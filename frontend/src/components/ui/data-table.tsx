import { intlLocale, t } from '@/i18n'
import type { ReactNode } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Button } from './button'
import { Checkbox } from './form'

export interface Column<T> {
  id: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** width utility, e.g. 'w-28' (table is table-fixed) */
  width?: string
  align?: 'left' | 'right' | 'center'
  /** hide below this breakpoint */
  hideBelow?: 'lg' | 'xl' | '2xl'
  className?: string
}

const hideCls = { lg: 'hidden lg:table-cell', xl: 'hidden xl:table-cell', '2xl': 'hidden 2xl:table-cell' }

/**
 * Table on ≥ md, card list below md (via `card`). Selection is optional.
 * The table never causes page-level horizontal scroll: it is table-fixed, columns truncate.
 */
export function DataTable<T extends { id: string }>({
  rows,
  columns,
  card,
  onRowClick,
  selected,
  onToggle,
  onToggleAll,
  rowClassName,
  className,
  dense,
  stickyTop = 'top-0',
}: {
  rows: T[]
  columns: Column<T>[]
  card?: (row: T) => ReactNode
  onRowClick?: (row: T) => void
  selected?: Set<string>
  onToggle?: (id: string) => void
  onToggleAll?: (checked: boolean) => void
  rowClassName?: (row: T) => string | undefined
  className?: string
  dense?: boolean
  stickyTop?: string
}) {
  const selectable = !!selected && !!onToggle
  const allOn = selectable && rows.length > 0 && rows.every((r) => selected!.has(r.id))
  const someOn = selectable && rows.some((r) => selected!.has(r.id))
  return (
    <div className={cn('min-w-0', className)}>
      {/* table ≥ md */}
      <table className={cn('w-full table-fixed border-separate border-spacing-0 text-[13px]', card && 'hidden md:table')}>
        <thead>
          <tr>
            {selectable && (
              <th className={cn('sticky z-10 w-11 border-b border-border bg-surface-2/95 pl-4 backdrop-blur', stickyTop)}>
                <span data-shot="select-all-page" className="inline-grid">
                <Checkbox
                  aria-label={t('common.selectPage')}
                  checked={allOn ? true : someOn ? 'indeterminate' : false}
                  onCheckedChange={(v) => onToggleAll?.(v)}
                />
                </span>
              </th>
            )}
            {columns.map((c) => (
              <th
                key={c.id}
                scope="col"
                className={cn(
                  'sticky z-10 h-10 truncate border-b border-border bg-surface-2/95 px-3 text-xs font-medium text-fg-3 backdrop-blur first:pl-4 last:pr-4',
                  stickyTop,
                  c.width,
                  c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                  c.hideBelow && hideCls[c.hideBelow],
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isSel = selectable && selected!.has(r.id)
            return (
              <tr
                key={r.id}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                className={cn(
                  'group transition-colors',
                  onRowClick && 'cursor-pointer',
                  isSel ? 'bg-primary-soft/60' : 'hover:bg-surface-2/70',
                  rowClassName?.(r),
                )}
              >
                {selectable && (
                  <td className="border-b border-border pl-4" onClick={(e) => e.stopPropagation()}>
                    <Checkbox aria-label={t('common.select')} checked={isSel} onCheckedChange={() => onToggle!(r.id)} />
                  </td>
                )}
                {columns.map((c) => (
                  <td
                    key={c.id}
                    className={cn(
                      'border-b border-border px-3 align-middle first:pl-4 last:pr-4',
                      dense ? 'py-2' : 'py-2.5',
                      c.align === 'right' ? 'text-right tabular' : c.align === 'center' ? 'text-center' : 'text-left',
                      c.hideBelow && hideCls[c.hideBelow],
                      c.className,
                    )}
                  >
                    {c.cell(r)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>

      {/* cards < md */}
      {card && (
        <ul className="divide-y divide-border md:hidden">
          {rows.map((r) => {
            const isSel = selectable && selected!.has(r.id)
            return (
              <li
                key={r.id}
                className={cn('flex min-w-0 gap-3 px-4 py-3', isSel && 'bg-primary-soft/60', onRowClick && 'active:bg-surface-2')}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
              >
                {selectable && (
                  <div className="-my-1 -ml-1 flex items-start pt-1" onClick={(e) => e.stopPropagation()}>
                    <Checkbox aria-label={t('common.select')} checked={isSel} onCheckedChange={() => onToggle!(r.id)} className="size-10" />
                  </div>
                )}
                <div className="min-w-0 flex-1">{card(r)}</div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  className,
}: {
  total: number
  limit: number
  offset: number
  onChange: (offset: number) => void
  className?: string
}) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(total, offset + limit)
  return (
    <div className={cn('flex items-center justify-between gap-3 px-4 py-3 text-[13px] text-fg-3', className)}>
      <span className="tabular">
        {t('common.pageRange', from.toLocaleString(intlLocale()), to.toLocaleString(intlLocale()), total.toLocaleString(intlLocale()))}
      </span>
      <div className="flex items-center gap-1">
        <Button size="icon-sm" variant="ghost" aria-label={t('common.prevPage')} disabled={page <= 1} onClick={() => onChange(Math.max(0, offset - limit))}>
          <ChevronLeft />
        </Button>
        <span className="min-w-16 text-center tabular text-fg-2">
          {page} / {pages}
        </span>
        <Button size="icon-sm" variant="ghost" aria-label={t('common.nextPage')} disabled={page >= pages} onClick={() => onChange(offset + limit)}>
          <ChevronRight />
        </Button>
      </div>
    </div>
  )
}
