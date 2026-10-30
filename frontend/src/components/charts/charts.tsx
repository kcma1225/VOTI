/* Small SVG/HTML chart kit following the dataviz method:
   thin marks (≤ 24px bars, 4px rounded data-ends, square at baseline), hairline solid grid, text in ink tokens,
   categorical hues in fixed order, hover tooltips, and a table-view twin via <ChartCard>. */
import { intlLocale, t } from '@/i18n'
import { useCallback, useLayoutEffect, useState, type ReactNode } from 'react'
import { Table2, BarChart3 } from 'lucide-react'
import { cn } from '@/lib/cn'
import { fmtCompact } from '@/lib/format'
import { Card, CardHeader } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { Tooltip as Tip } from '@/components/ui/overlay'

export const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)', 'var(--series-6)', 'var(--series-7)', 'var(--series-8)']
export const ORDINAL = ['var(--seq-3)', 'var(--seq-5)', 'var(--seq-7)']

export function useSize<T extends HTMLElement>() {
  // callback ref so it also works for elements that mount after the first render
  const [el, setEl] = useState<T | null>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  const ref = useCallback((node: T | null) => setEl(node), [])
  return [ref, w] as const
}

/* ───────────── ChartCard with table-view toggle ───────────── */
export function ChartCard({
  title,
  description,
  children,
  table,
  actions,
  className,
  bodyClassName,
}: {
  title: ReactNode
  description?: ReactNode
  children: ReactNode
  table?: { columns: string[]; rows: (string | number)[][] }
  actions?: ReactNode
  className?: string
  bodyClassName?: string
}) {
  const [asTable, setAsTable] = useState(false)
  return (
    <Card className={cn('flex flex-col', className)}>
      <CardHeader
        title={title}
        description={description}
        actions={
          <>
            {actions}
            {table && (
              <Tip content={asTable ? t('people.charts.showChart') : t('people.charts.showTable')}>
                <Button size="icon-sm" variant="ghost" aria-label={asTable ? t('people.charts.showChart') : t('people.charts.showTable')} onClick={() => setAsTable((v) => !v)}>
                  {asTable ? <BarChart3 /> : <Table2 />}
                </Button>
              </Tip>
            )}
          </>
        }
      />
      <div className={cn('min-w-0 flex-1 px-4 pb-4 pt-3 sm:px-5 sm:pb-5', bodyClassName)}>
        {asTable && table ? <DataTableView {...table} /> : children}
      </div>
    </Card>
  )
}

function DataTableView({ columns, rows }: { columns: string[]; rows: (string | number)[][] }) {
  return (
    <div className="max-h-80 overflow-auto rounded-lg border border-border scrollbar-thin">
      <table className="w-full text-[13px]">
        <thead className="sticky top-0 bg-surface-2">
          <tr>
            {columns.map((c, i) => (
              <th key={i} className={cn('px-3 py-2 font-medium text-fg-3', i ? 'text-right' : 'text-left')}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {r.map((v, j) => (
                <td key={j} className={cn('px-3 py-1.5', j ? 'text-right tabular text-fg-2' : 'text-fg')}>
                  {typeof v === 'number' ? v.toLocaleString(intlLocale(), { maximumFractionDigits: 2 }) : v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ───────────── shared tooltip ───────────── */
export function HoverTip({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  const left = Math.min(Math.max(x, 70), w - 70)
  return (
    <div
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-border bg-surface px-2.5 py-1.5 text-xs shadow-md"
      style={{ left, top: y - 8 }}
    >
      {children}
    </div>
  )
}

function niceMax(v: number) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  const n = v / p
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p
}
const compactNum = (n: number) => fmtCompact(n)

/* ───────────── Histogram (columns) ───────────── */
export function Histogram({
  bins,
  binLabel,
  height = 180,
  threshold,
  unit,
  color = 'var(--series-1)',
  highlight,
}: {
  bins: { bin: number; count: number }[]
  binLabel: (b: number, i: number) => string
  height?: number
  threshold?: { value: number; label: string; binWidth: number; start: number }
  unit?: string
  color?: string
  highlight?: (b: number) => boolean
}) {
  const [ref, w] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const padL = 36
  const padB = 24
  const plotH = height - padB
  const plotW = Math.max(0, w - padL - 4)
  const max = niceMax(Math.max(1, ...bins.map((b) => b.count)))
  const band = bins.length ? plotW / bins.length : 0
  const barW = Math.max(2, Math.min(24, band - 2))
  const y = (v: number) => plotH - (v / max) * (plotH - 8)
  const ticks = [0, max / 2, max]
  const labelEvery = Math.max(1, Math.ceil(bins.length / Math.max(2, Math.floor(plotW / 44))))
  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {w > 0 && (
        <svg width={w} height={height} className="block overflow-visible" role="img">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={w} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" strokeWidth={1} />
              <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" className="fill-fg-3 text-[10px] tabular">
                {compactNum(t)}
              </text>
            </g>
          ))}
          {bins.map((b, i) => {
            const x = padL + i * band + (band - barW) / 2
            const h = Math.max(b.count ? 2 : 0, plotH - y(b.count))
            const hl = highlight ? highlight(b.bin) : true
            const r = Math.min(4, barW / 2, h)
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={padL + i * band} y={0} width={band} height={plotH} fill="transparent" />
                {h > 0 && (
                  <path
                    d={`M${x},${plotH} L${x},${plotH - h + r} Q${x},${plotH - h} ${x + r},${plotH - h} L${x + barW - r},${plotH - h} Q${x + barW},${plotH - h} ${x + barW},${plotH - h + r} L${x + barW},${plotH} Z`}
                    fill={hl ? color : 'var(--series-muted)'}
                    opacity={hover === null || hover === i ? 1 : 0.55}
                  />
                )}
                {i % labelEvery === 0 && (
                  <text x={padL + i * band + band / 2} y={height - 6} textAnchor="middle" className="fill-fg-3 text-[10px] tabular">
                    {binLabel(b.bin, i)}
                  </text>
                )}
              </g>
            )
          })}
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--chart-axis)" strokeWidth={1} />
          {threshold && plotW > 0 && (
            <g>
              {(() => {
                const tx = padL + ((threshold.value - threshold.start) / threshold.binWidth) * band
                return (
                  <>
                    <line x1={tx} x2={tx} y1={4} y2={plotH} stroke="var(--fg-2)" strokeWidth={1.5} />
                    <text x={tx + 4} y={12} className="fill-fg-2 text-[10px] font-medium">
                      {threshold.label}
                    </text>
                  </>
                )
              })()}
            </g>
          )}
        </svg>
      )}
      {hover !== null && bins[hover] && (
        <HoverTip x={padL + hover * band + band / 2} y={y(bins[hover].count)} w={w}>
          <div className="text-fg-3">{t('people.charts.binFrom', binLabel(bins[hover].bin, hover))}</div>
          <div className="font-medium tabular text-fg">
            {unit ? `${bins[hover].count.toLocaleString(intlLocale())} ${unit}` : t('people.charts.videos', bins[hover].count)}
          </div>
        </HoverTip>
      )}
    </div>
  )
}

/* ───────────── Horizontal bars (HTML) ───────────── */
export interface BarRow {
  key: string
  label: ReactNode
  value: number
  color?: string
  sub?: ReactNode
  href?: string
}
export function HBars({
  rows,
  format,
  max,
  className,
  barClassName,
}: {
  rows: BarRow[]
  format: (v: number) => string
  max?: number
  className?: string
  barClassName?: string
}) {
  const m = max ?? Math.max(1e-9, ...rows.map((r) => r.value))
  return (
    <ul className={cn('space-y-2.5', className)}>
      {rows.map((r) => (
        <li key={r.key} className="group min-w-0">
          <div className="mb-1 flex min-w-0 items-baseline justify-between gap-3 text-[13px]">
            <span className="flex min-w-0 items-center gap-2 text-fg">
              {r.color && <span className="size-2.5 shrink-0 rounded-[3px]" style={{ background: r.color }} />}
              <span className="truncate">{r.label}</span>
              {r.sub && <span className="hidden shrink-0 text-xs text-fg-3 sm:inline">{r.sub}</span>}
            </span>
            <span className="shrink-0 tabular text-fg-2">{format(r.value)}</span>
          </div>
          <div className="h-2 w-full rounded-full bg-surface-2">
            <div
              className={cn('h-full rounded-full transition-[width] duration-500', barClassName)}
              style={{ width: `${Math.max(r.value > 0 ? 1 : 0, (r.value / m) * 100)}%`, background: r.color ?? 'var(--series-1)' }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

/* ───────────── 100% stacked bar with legend ───────────── */
export function StackBar({
  parts,
  format,
  height = 14,
  className,
}: {
  parts: { key: string; label: string; value: number; color: string }[]
  format: (v: number) => string
  height?: number
  className?: string
}) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1
  return (
    <div className={className}>
      <div className="flex w-full gap-[2px] overflow-hidden rounded-md" style={{ height }}>
        {parts
          .filter((p) => p.value > 0)
          .map((p) => (
            <Tip key={p.key} content={t('people.charts.share', String(p.label), format(p.value), ((p.value / total) * 100).toFixed(1))}>
              <div className="h-full min-w-[3px] transition-[flex-grow] duration-500" style={{ flexGrow: p.value, background: p.color }} />
            </Tip>
          ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">
        {parts.filter((p) => p.value > 0).map((p) => (
          <li key={p.key} className="flex items-center gap-2">
            <span className="size-2.5 rounded-[3px]" style={{ background: p.color }} />
            <span className="text-fg-2">{p.label}</span>
            <span className="tabular text-fg">{((p.value / total) * 100).toFixed(1)}%</span>
            <span className="tabular text-xs text-fg-3">{format(p.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/* ───────────── Ranked columns (long tail) ───────────── */
export function RankColumns({
  values,
  labels,
  height = 200,
  format,
  highlightTop = 0,
}: {
  values: number[]
  labels: string[]
  height?: number
  format: (v: number) => string
  highlightTop?: number
}) {
  const [ref, w] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const padL = 36
  const padB = 20
  const plotH = height - padB
  const plotW = Math.max(0, w - padL - 2)
  const max = niceMax(Math.max(1e-9, ...values))
  const band = values.length ? plotW / values.length : 0
  const barW = Math.max(1, Math.min(24, band - (band > 4 ? 2 : 0.5)))
  const y = (v: number) => plotH - (v / max) * (plotH - 6)
  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {w > 0 && (
        <svg width={w} height={height} className="block" role="img">
          {[0, max / 2, max].map((t) => (
            <g key={t}>
              <line x1={padL} x2={w} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" />
              <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" className="fill-fg-3 text-[10px] tabular">
                {format(t)}
              </text>
            </g>
          ))}
          {values.map((v, i) => {
            const x = padL + i * band + (band - barW) / 2
            const h = Math.max(1, plotH - y(v))
            const r = Math.min(3, barW / 2, h)
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={padL + i * band} y={0} width={band} height={plotH} fill="transparent" />
                <path
                  d={`M${x},${plotH} L${x},${plotH - h + r} Q${x},${plotH - h} ${x + r},${plotH - h} L${x + barW - r},${plotH - h} Q${x + barW},${plotH - h} ${x + barW},${plotH - h + r} L${x + barW},${plotH} Z`}
                  fill={i < highlightTop ? 'var(--series-1)' : 'var(--seq-3)'}
                  opacity={hover === null || hover === i ? 1 : 0.6}
                />
              </g>
            )
          })}
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--chart-axis)" />
          <text x={padL} y={height - 4} className="fill-fg-3 text-[10px]">
            {t('people.charts.mostHours')}
          </text>
          <text x={w} y={height - 4} textAnchor="end" className="fill-fg-3 text-[10px]">
            {t('people.charts.least')}
          </text>
        </svg>
      )}
      {hover !== null && (
        <HoverTip x={padL + hover * band + band / 2} y={y(values[hover])} w={w}>
          <div className="text-fg-3">
            {t('people.charts.rank', hover + 1)} · {labels[hover]}
          </div>
          <div className="font-medium tabular text-fg">{format(values[hover])}</div>
        </HoverTip>
      )}
    </div>
  )
}

/* ───────────── Lorenz curve (one axis: share of speakers vs share of hours) ───────────── */
export function Lorenz({ values, height = 200 }: { values: number[]; height?: number }) {
  const [ref, w] = useSize<HTMLDivElement>()
  const sorted = [...values].sort((a, b) => a - b)
  const total = sorted.reduce((a, b) => a + b, 0) || 1
  const pts: [number, number][] = [[0, 0]]
  let c = 0
  sorted.forEach((v, i) => {
    c += v
    pts.push([(i + 1) / sorted.length, c / total])
  })
  const pad = 40
  const size = Math.max(0, Math.min(w, height + 40) - pad - 6)
  const X = (x: number) => pad + x * size
  const Y = (y: number) => 6 + size - y * size
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(' ')
  return (
    <div ref={ref} className="w-full" style={{ height: size + 30 }}>
      {w > 0 && (
        <svg width={w} height={size + 30} className="block" role="img" aria-label={t('people.charts.lorenz')}>
          {[0, 0.5, 1].map((t) => (
            <g key={t}>
              <line x1={X(0)} x2={X(1)} y1={Y(t)} y2={Y(t)} stroke="var(--chart-grid)" />
              <text x={X(0) - 6} y={Y(t)} dy="0.32em" textAnchor="end" className="fill-fg-3 text-[10px]">
                {t * 100}%
              </text>
              <text x={X(t)} y={Y(0) + 14} textAnchor="middle" className="fill-fg-3 text-[10px]">
                {t * 100}%
              </text>
            </g>
          ))}
          <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} stroke="var(--chart-axis)" strokeWidth={1} />
          <path d={`${d} L${X(1)},${Y(0)} Z`} fill="var(--series-1)" opacity={0.1} />
          <path d={d} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />
          <text x={X(0.52)} y={Y(0.6)} className="fill-fg-3 text-[10px]" transform={`rotate(-${Math.atan(1) * (180 / Math.PI)} ${X(0.52)} ${Y(0.6)})`}>
            {t('people.charts.equality')}
          </text>
        </svg>
      )}
    </div>
  )
}

/* ───────────── Meter for a single 0..1 or bounded value ───────────── */
export function Meter({ value, min = 0, max = 1, tone = 'primary', className }: { value: number | null; min?: number; max?: number; tone?: 'primary' | 'success' | 'warning' | 'danger'; className?: string }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(1, (value - min) / (max - min))) * 100
  const fill = { primary: 'bg-primary', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' }[tone]
  const track = { primary: 'bg-primary-soft', success: 'bg-success-soft', warning: 'bg-warning-soft', danger: 'bg-danger-soft' }[tone]
  return (
    <div className={cn('h-1.5 w-full overflow-hidden rounded-full', track, className)}>
      <div className={cn('h-full rounded-full', fill)} style={{ width: `${pct}%` }} />
    </div>
  )
}
