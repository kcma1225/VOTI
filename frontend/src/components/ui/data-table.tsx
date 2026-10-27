import { intlLocale, t } from '@/i18n'
import { useCallback, useEffect, useRef, useState, type MouseEvent as RMouseEvent, type ReactNode } from 'react'
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
  onToggleRange,
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
  /** Enables Shift+click range selection (Gmail / file-manager semantics): the clicked row's new state is applied to
   * every row between the anchor (last plainly clicked row) and it, in the current page order. */
  onToggleRange?: (ids: string[], on: boolean) => void
}) {
  const selectable = !!selected && !!onToggle
  const range = useRangeSelect(rows, selectable ? selected! : undefined, selectable ? onToggle : undefined, onToggleRange)
  const allOn = selectable && rows.length > 0 && rows.every((r) => selected!.has(r.id))
  const someOn = selectable && rows.some((r) => selected!.has(r.id))
  return (
    <div className={cn('relative min-w-0', range.shift && range.enabled && 'select-none', className)} onMouseLeave={range.leave} onMouseMove={range.move}>
      {range.hint && (
        <div
          role="status"
          data-shot="range-hint"
          className={cn(
            'pointer-events-none fixed z-50 rounded-md px-2 py-1 text-xs font-medium shadow-md',
            range.hint.on ? 'bg-primary text-primary-fg' : 'bg-danger text-white',
          )}
          style={{ left: range.hint.x + 14, top: range.hint.y + 14 }}
        >
          {range.hint.on ? t('common.shiftSelect', range.hint.n) : t('common.shiftDeselect', range.hint.n)}
        </div>
      )}
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
            const pv = range.preview(r.id)
            return (
              <tr
                key={r.id}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
                onMouseEnter={() => range.hover(r.id)}
                data-row-id={r.id}
                data-range-preview={pv ?? undefined}
                className={cn(
                  'group transition-colors',
                  onRowClick && 'cursor-pointer',
                  pv === 'on' ? 'bg-primary-soft' : pv === 'off' ? 'bg-danger-soft/60 text-fg-3' : isSel ? 'bg-primary-soft/60' : 'hover:bg-surface-2/70',
                  rowClassName?.(r),
                )}
              >
                {selectable && (
                  <td className="border-b border-border pl-4" onClick={(e) => e.stopPropagation()} onMouseDown={range.mouseDown} onClickCapture={range.clickCapture}>
                    <RangeCheckbox checked={pv ? pv === 'on' : isSel} preview={pv} onChange={() => range.toggle(r.id)} />
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
            const pv = range.preview(r.id)
            return (
              <li
                key={r.id}
                data-range-preview={pv ?? undefined}
                data-row-id={r.id}
                onMouseEnter={() => range.hover(r.id)}
                className={cn('flex min-w-0 gap-3 px-4 py-3', pv === 'on' ? 'bg-primary-soft' : pv === 'off' ? 'bg-danger-soft/60' : isSel && 'bg-primary-soft/60', onRowClick && 'active:bg-surface-2')}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
              >
                {selectable && (
                  <div className="-my-1 -ml-1 flex items-start pt-1" onClick={(e) => e.stopPropagation()} onMouseDown={range.mouseDown} onClickCapture={range.clickCapture}>
                    <RangeCheckbox checked={pv ? pv === 'on' : isSel} preview={pv} onChange={() => range.toggle(r.id)} className="size-10" />
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

/** Row checkbox with the Shift-range preview ring (dashed: will select / will deselect). */
function RangeCheckbox({ checked, preview, onChange, className }: { checked: boolean; preview: 'on' | 'off' | null; onChange: () => void; className?: string }) {
  return (
    <span
      data-preview={preview ?? undefined}
      className={cn(
        'inline-grid rounded-md',
        preview === 'on' && 'outline-2 outline-offset-2 outline-dashed outline-primary',
        preview === 'off' && 'opacity-70 outline-2 outline-offset-2 outline-dashed outline-danger',
      )}
    >
      <Checkbox aria-label={t('common.select')} checked={checked} onCheckedChange={onChange} className={className} />
    </span>
  )
}

/** Shift+click range selection with a live preview while Shift is held (mouse/keyboard only; touch unchanged). */
function useRangeSelect<T extends { id: string }>(
  rows: T[],
  selected: Set<string> | undefined,
  onToggle: ((id: string) => void) | undefined,
  onRange: ((ids: string[], on: boolean) => void) | undefined,
) {
  const enabled = !!selected && !!onToggle && !!onRange
  const anchor = useRef<string | null>(null)
  const clickShift = useRef(false)
  const [shift, setShift] = useState(false)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const shiftRef = useRef(false)

  useEffect(() => {
    if (!enabled) return
    const down = (e: KeyboardEvent) => {
      if (e.key === 'Shift') ((shiftRef.current = true), setShift(true))
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === 'Shift') ((shiftRef.current = false), setShift(false))
    }
    const blur = () => ((shiftRef.current = false), setShift(false), setHoverId(null))
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
    }
  }, [enabled])
  // a different page / filter: the anchor no longer means anything
  const ids = rows.map((r) => r.id).join(',')
  useEffect(() => {
    if (anchor.current && !rows.some((r) => r.id === anchor.current)) anchor.current = null
  }, [ids]) // eslint-disable-line react-hooks/exhaustive-deps

  const span = useCallback(
    (to: string): string[] | null => {
      const a = anchor.current
      if (!a || a === to) return null
      const i = rows.findIndex((r) => r.id === a)
      const j = rows.findIndex((r) => r.id === to)
      if (i < 0 || j < 0) return null
      return rows.slice(Math.min(i, j), Math.max(i, j) + 1).map((r) => r.id)
    },
    [rows],
  )

  const toggle = (id: string) => {
    if (!enabled) return onToggle?.(id)
    const isShift = clickShift.current || shiftRef.current
    clickShift.current = false
    const rng = isShift ? span(id) : null
    if (rng) onRange!(rng, !selected!.has(id))
    else onToggle!(id)
    anchor.current = id
  }

  // preview: the rows whose state would change if the hovered row were shift-clicked now
  const active = enabled && shift && hoverId ? span(hoverId) : null
  const willOn = active ? !selected!.has(hoverId!) : false
  const changing = active ? new Set(active.filter((x) => selected!.has(x) !== willOn)) : null
  return {
    enabled,
    shift,
    toggle,
    preview: (id: string): 'on' | 'off' | null => (changing?.has(id) ? (willOn ? 'on' : 'off') : null),
    hint: changing && changing.size && pos ? { n: changing.size, on: willOn, ...pos } : null,
    hover: (id: string) => enabled && setHoverId(id),
    leave: () => (setHoverId(null), setPos(null)),
    move: (e: RMouseEvent) => {
      if (!enabled) return
      const id = (e.target as HTMLElement).closest?.('[data-row-id]')?.getAttribute('data-row-id')
      if (id && id !== hoverId) setHoverId(id)
      if (shiftRef.current) setPos({ x: e.clientX, y: e.clientY })
    },
    /** no text selection on Shift+click */
    mouseDown: (e: RMouseEvent) => {
      if (e.shiftKey) e.preventDefault()
    },
    clickCapture: (e: RMouseEvent) => {
      clickShift.current = e.shiftKey
    },
  }
}
