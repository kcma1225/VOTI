/* Speaker similarity: a voice map (t-SNE of cluster centroids, nearest-neighbour links) and a similarity matrix.
   Colour = voice sex (fixed: male series-1, female series-2, undetermined muted); size = hours; links and cells
   encode similarity in one hue. The side panel is the non-hover path to every value shown on hover. */
import { useMemo, useState, type PointerEvent } from 'react'
import { Link } from 'react-router'
import { ArrowUpRight, BadgeCheck, X } from 'lucide-react'
import type { SimilarityNode, SpeakerSimilarity } from '@/api/types'
import { useSpeakerSimilarity } from '@/api/queries'
import { t } from '@/i18n'
import { cn } from '@/lib/cn'
import { fmtHours } from '@/lib/format'
import { HoverTip, useSize } from '@/components/charts/charts'
import { Button } from '@/components/ui/button'
import { Avatar, Card, CardBody, CardHeader } from '@/components/ui/primitives'
import { Field, Segmented, Select, Slider } from '@/components/ui/form'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { speakerName } from '@/components/domain/speakers'
import { Waypoints } from 'lucide-react'

const SEX_COLOR = {
  male: 'var(--series-1)',
  female: 'var(--series-2)',
  none: 'var(--series-muted)',
} as const
const sexColor = (n: SimilarityNode) => SEX_COLOR[n.sex ?? 'none']
const fmtSim = (v: number) => v.toFixed(2)
const MATRIX_N = 40
const LABELLED = 10

type Edge = SpeakerSimilarity['edges'][number]

function neighboursOf(edges: Edge[], i: number) {
  return edges
    .filter((e) => e.a === i || e.b === i)
    .map((e) => ({ j: e.a === i ? e.b : e.a, sim: e.sim }))
    .sort((x, y) => y.sim - x.sim)
}

export function SpeakerSimilarityView({ channelId }: { channelId?: string }) {
  const [view, setView] = useState<'map' | 'matrix'>('map')
  const [limit, setLimit] = useState(120)
  const [minSim, setMinSim] = useState<number | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const q = useSpeakerSimilarity({
    channel_id: channelId,
    limit,
    matrix_n: MATRIX_N,
  })
  const d = q.data
  const threshold = d?.threshold ?? 0.6
  const linkMin = minSim ?? Math.max(0.3, Math.round((threshold - 0.1) * 100) / 100)
  const sel = selected !== null && d && selected < d.nodes.length ? selected : null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
        <Segmented
          aria-label={t('people.map.viewMap')}
          value={view}
          onChange={setView}
          options={[
            { value: 'map', label: t('people.map.viewMap') },
            { value: 'matrix', label: t('people.map.viewMatrix') },
          ]}
        />
        {view === 'map' && (
          <>
            <Select
              value={String(limit)}
              onChange={(e) => (setLimit(Number(e.target.value)), setSelected(null))}
              className="sm:w-32"
              aria-label={t('people.map.top')}
            >
              {[50, 120, 200, 300].map((n) => (
                <option key={n} value={n}>
                  {t('people.map.topN', n)}
                </option>
              ))}
            </Select>
            <Field label={t('people.map.minSim')} aside={fmtSim(linkMin)} className="sm:w-56">
              <Slider aria-label={t('people.map.minSim')} value={[linkMin]} onValueChange={([v]) => setMinSim(v)} min={0.2} max={0.95} step={0.01} />
            </Field>
          </>
        )}
      </div>

      {q.isError ? (
        <Card>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Card>
      ) : !d ? (
        <Card>
          <ListSkeleton rows={8} />
        </Card>
      ) : !d.nodes.length ? (
        <Card>
          <EmptyState icon={<Waypoints />} title={t('people.map.empty')} description={t('people.speakers.noRunDesc')} />
        </Card>
      ) : (
        <div className={cn('grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]', q.isPlaceholderData && 'opacity-60 transition-opacity')}>
          <Card className="min-w-0">
            <CardHeader
              title={view === 'map' ? t('people.map.mapTitle') : t('people.map.matrixTitle')}
              description={view === 'map' ? t('people.map.mapDesc') : t('people.map.matrixDesc', d.matrix.length)}
            />
            <CardBody className="pt-1">
              {view === 'map' ? (
                <VoiceMap d={d} linkMin={linkMin} selected={sel} onSelect={setSelected} />
              ) : (
                <SimilarityMatrix d={d} threshold={threshold} onSelect={setSelected} />
              )}
            </CardBody>
          </Card>
          <SidePanel d={d} selected={sel} onSelect={setSelected} />
        </div>
      )}
    </div>
  )
}

/* ───────────── voice map ───────────── */

function VoiceMap({
  d,
  linkMin,
  selected,
  onSelect,
}: {
  d: SpeakerSimilarity
  linkMin: number
  selected: number | null
  onSelect: (i: number | null) => void
}) {
  const [ref, w] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const h = Math.round(Math.min(620, Math.max(340, w * 0.66)))
  const pad = 22
  const maxSec = Math.max(1, ...d.nodes.map((n) => n.total_sec))
  const pts = useMemo(
    () =>
      d.nodes.map((n) => ({
        x: pad + n.x * Math.max(0, w - 2 * pad),
        y: pad + n.y * (h - 2 * pad),
        r: 3.5 + 13 * Math.sqrt(n.total_sec / maxSec),
      })),
    [d.nodes, w, h, maxSec],
  )
  const links = d.edges.filter((e) => e.sim >= linkMin)
  const active = hover ?? selected
  const near = useMemo(() => {
    if (active === null) return null
    const s = new Set<number>([active])
    for (const e of links) if (e.a === active || e.b === active) s.add(e.a === active ? e.b : e.a)
    return s
  }, [active, links])
  const labels = placeLabels(d, pts, w, h, active, near)

  function nearest(e: PointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect()
    const px = e.clientX - box.left
    const py = e.clientY - box.top
    let best: number | null = null
    let bd = Infinity
    pts.forEach((p, i) => {
      const dd = Math.hypot(p.x - px, p.y - py) - p.r
      if (dd < bd) [best, bd] = [i, dd]
    })
    return bd <= 18 ? best : null
  }

  const tipNode = hover !== null ? d.nodes[hover] : null
  return (
    <div>
      <div ref={ref} className="relative w-full" style={{ height: h }}>
        {w > 0 && (
          <svg
            width={w}
            height={h}
            className="block touch-pan-y select-none"
            role="img"
            aria-label={t('people.map.aria')}
            onPointerMove={(e) => setHover(nearest(e))}
            onPointerLeave={() => setHover(null)}
            onClick={(e) => {
              const i = nearest(e as unknown as PointerEvent<SVGSVGElement>)
              onSelect(i === selected ? null : i)
            }}
            style={{ cursor: hover !== null ? 'pointer' : 'default' }}
          >
            <g>
              {links.map((e) => {
                const on = active !== null && (e.a === active || e.b === active)
                const k = Math.min(1, Math.max(0, (e.sim - linkMin) / Math.max(0.05, 1 - linkMin)))
                return (
                  <line
                    key={`${e.a}-${e.b}`}
                    x1={pts[e.a].x}
                    y1={pts[e.a].y}
                    x2={pts[e.b].x}
                    y2={pts[e.b].y}
                    stroke={on ? 'var(--fg)' : 'var(--fg-3)'}
                    strokeWidth={on ? 1.5 + 1.5 * k : 0.75 + 1.25 * k}
                    strokeOpacity={on ? 0.85 : active !== null ? 0.06 : 0.12 + 0.4 * k}
                  />
                )
              })}
            </g>
            <g>
              {pts.map((p, i) => {
                const n = d.nodes[i]
                const dim = near !== null && !near.has(i)
                return (
                  <circle
                    key={n.id}
                    cx={p.x}
                    cy={p.y}
                    r={p.r}
                    fill={sexColor(n)}
                    fillOpacity={dim ? 0.25 : 0.9}
                    stroke={i === active ? 'var(--fg)' : 'var(--surface)'}
                    strokeWidth={2}
                  />
                )
              })}
            </g>
            <g className="pointer-events-none">
              {labels.map(({ i, x, anchor, text }) => (
                <text
                  key={i}
                  x={x}
                  y={pts[i].y}
                  dy="0.32em"
                  textAnchor={anchor}
                  className={cn('text-[11px]', near && !near.has(i) ? 'fill-fg-3' : i === active ? 'fill-fg font-semibold' : 'fill-fg-2 font-medium')}
                  style={{ paintOrder: 'stroke', stroke: 'var(--surface)', strokeWidth: 3, strokeLinejoin: 'round' }}
                >
                  {text}
                </text>
              ))}
            </g>
          </svg>
        )}
        {tipNode && hover !== null && (
          <HoverTip x={pts[hover].x} y={pts[hover].y - pts[hover].r} w={w}>
            <div className="font-semibold text-fg">{speakerName(tipNode)}</div>
            <div className="text-fg-3">{t('people.map.hours', fmtHours(tipNode.total_sec), tipNode.media_count)}</div>
            {neighboursOf(d.edges, hover)
              .slice(0, 3)
              .map(({ j, sim }) => (
                <div key={j} className="flex items-center justify-between gap-3">
                  <span className="text-fg-2">{speakerName(d.nodes[j])}</span>
                  <span className="font-semibold tabular text-fg">{fmtSim(sim)}</span>
                </div>
              ))}
          </HoverTip>
        )}
      </div>
      <MapLegend />
    </div>
  )
}

/** Greedy label placement: the active speaker, then its neighbours (most similar first), then the biggest speakers;
    each label goes right of its dot, else left, and is dropped when it would overlap a placed label. */
function placeLabels(d: SpeakerSimilarity, pts: { x: number; y: number; r: number }[], w: number, h: number, active: number | null, near: Set<number> | null) {
  const order: number[] = []
  const seen = new Set<number>()
  const push = (i: number) => !seen.has(i) && (seen.add(i), order.push(i))
  if (active !== null) {
    push(active)
    for (const { j } of neighboursOf(d.edges, active)) if (near?.has(j)) push(j)
  }
  for (let i = 0; i < Math.min(LABELLED, d.nodes.length); i++) push(i)
  const boxes: [number, number, number, number][] = []
  const hit = (b: [number, number, number, number]) => boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1])
  const out: { i: number; x: number; anchor: 'start' | 'end'; text: string }[] = []
  for (const i of order) {
    const p = pts[i]
    const name = speakerName(d.nodes[i]).slice(0, 14)
    const sim = active !== null && i !== active && near?.has(i) ? simBetween(d.edges, active, i) : null
    const text = sim !== null ? `${name} · ${fmtSim(sim)}` : name
    const tw = [...text].reduce((s, ch) => s + (ch.charCodeAt(0) > 0x2e80 ? 11 : 6.2), 0)
    const y0 = p.y - 7
    const y1 = p.y + 7
    const right: [number, number, number, number] = [p.x + p.r + 3, y0, p.x + p.r + 5 + tw, y1]
    const left: [number, number, number, number] = [p.x - p.r - 5 - tw, y0, p.x - p.r - 3, y1]
    const fits = (b: [number, number, number, number]) => b[0] >= 0 && b[2] <= w && b[1] >= 0 && b[3] <= h && !hit(b)
    const box = fits(right) ? right : fits(left) ? left : null
    if (!box && i !== active) continue
    const b = box ?? right
    boxes.push(b)
    out.push({ i, x: b === right ? p.x + p.r + 4 : p.x - p.r - 4, anchor: b === right ? 'start' : 'end', text })
  }
  return out
}

function simBetween(edges: Edge[], a: number, b: number) {
  const e = edges.find((x) => (x.a === a && x.b === b) || (x.a === b && x.b === a))
  return e?.sim ?? null
}

function MapLegend() {
  const dot = (c: string, label: string) => (
    <span className="inline-flex items-center gap-1.5">
      <span className="size-2.5 rounded-full" style={{ background: c }} />
      {label}
    </span>
  )
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-fg-2">
      {dot(SEX_COLOR.male, t('people.map.legendMale'))}
      {dot(SEX_COLOR.female, t('people.map.legendFemale'))}
      {dot(SEX_COLOR.none, t('people.map.legendUnknown'))}
      <span className="inline-flex items-center gap-1.5">
        <svg width="22" height="12" aria-hidden>
          <circle cx="4" cy="6" r="3" fill="var(--fg-3)" />
          <circle cx="15" cy="6" r="5.5" fill="var(--fg-3)" />
        </svg>
        {t('people.map.legendSize')}
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-0.5 w-5 rounded-full bg-fg-3" />
        {t('people.map.legendLine')}
      </span>
    </div>
  )
}

/* ───────────── similarity matrix ───────────── */

const cellColor = (v: number) => {
  const k = Math.min(1, Math.max(0, (v - 0.1) / 0.9))
  return `color-mix(in oklab, var(--seq-7) ${Math.round(k * 100)}%, var(--surface-2))`
}

function SimilarityMatrix({ d, threshold, onSelect }: { d: SpeakerSimilarity; threshold: number; onSelect: (i: number | null) => void }) {
  const [ref, w] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<[number, number] | null>(null)
  const order = d.order.length ? d.order : d.matrix.map((_, i) => i)
  const m = order.length
  const labelW = w < 480 ? 76 : 120
  const cell = Math.max(5, Math.floor((w - labelW) / Math.max(1, m)))
  const gap = cell >= 9 ? 1 : 0
  const size = cell * m
  return (
    <div>
      <div ref={ref} className="relative w-full" style={{ height: size + 4 }}>
        {w > 0 && (
          <svg
            width={labelW + size}
            height={size + 4}
            className="block"
            role="img"
            aria-label={t('people.map.ariaMatrix')}
            onPointerLeave={() => setHover(null)}
          >
            {order.map((i, r) => (
              <text
                key={i}
                x={labelW - 6}
                y={r * cell + cell / 2}
                dy="0.32em"
                textAnchor="end"
                className={cn('text-[10px]', hover && (hover[0] === r || hover[1] === r) ? 'fill-fg font-semibold' : 'fill-fg-3')}
              >
                {cell >= 8 ? speakerName(d.nodes[i]).slice(0, w < 480 ? 8 : 14) : ''}
              </text>
            ))}
            {order.map((i, r) =>
              order.map((j, c) => {
                const v = d.matrix[i][j]
                const strong = i !== j && v >= threshold
                return (
                  <rect
                    key={`${r}-${c}`}
                    x={labelW + c * cell}
                    y={r * cell}
                    width={cell - gap}
                    height={cell - gap}
                    rx={cell >= 9 ? 1.5 : 0}
                    fill={cellColor(v)}
                    stroke={strong ? 'var(--fg)' : hover && (hover[0] === r || hover[1] === c) ? 'var(--fg-3)' : 'none'}
                    strokeWidth={strong ? 1 : 0.5}
                    onPointerEnter={() => setHover([r, c])}
                    onClick={() => onSelect(i)}
                    style={{ cursor: 'pointer' }}
                  />
                )
              }),
            )}
          </svg>
        )}
        {hover && (
          <HoverTip x={labelW + hover[1] * cell + cell / 2} y={hover[0] * cell} w={w}>
            <div className="font-semibold tabular text-fg">{t('people.map.sim', fmtSim(d.matrix[order[hover[0]]][order[hover[1]]]))}</div>
            <div className="text-fg-2">
              {speakerName(d.nodes[order[hover[0]]])} ↔ {speakerName(d.nodes[order[hover[1]]])}
            </div>
          </HoverTip>
        )}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-fg-2">
        <span className="inline-flex items-center gap-2">
          <span className="tabular text-fg-3">0.1</span>
          <span
            className="h-2 w-28 rounded-full"
            style={{
              background: `linear-gradient(90deg, ${cellColor(0.1)}, ${cellColor(0.55)}, ${cellColor(1)})`,
            }}
          />
          <span className="tabular text-fg-3">1.0</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-[2px] border border-fg" />
          {t('people.map.clusterLine')} ≥ {fmtSim(threshold)}
        </span>
      </div>
    </div>
  )
}

/* ───────────── side panel ───────────── */

function SidePanel({ d, selected, onSelect }: { d: SpeakerSimilarity; selected: number | null; onSelect: (i: number | null) => void }) {
  const threshold = d.threshold ?? 0.6
  if (selected === null) {
    const pairs = d.edges.slice(0, 10)
    return (
      <Card className="min-w-0 self-start">
        <CardHeader title={t('people.map.topPairs')} description={t('people.map.pick')} />
        <ul className="divide-y divide-border px-2 pb-2">
          {pairs.map((e) => (
            <li key={`${e.a}-${e.b}`}>
              <button
                type="button"
                onClick={() => onSelect(e.a)}
                className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-[13px] hover:bg-surface-2"
              >
                <span className="min-w-0 flex-1 truncate">
                  {speakerName(d.nodes[e.a])} <span className="text-fg-3">↔</span> {speakerName(d.nodes[e.b])}
                </span>
                <SimValue v={e.sim} threshold={threshold} />
              </button>
            </li>
          ))}
        </ul>
      </Card>
    )
  }
  const n = d.nodes[selected]
  const list = neighboursOf(d.edges, selected).slice(0, 8)
  return (
    <Card className="min-w-0 self-start">
      <div className="flex items-start gap-3 px-4 pt-4">
        <Avatar name={speakerName(n)} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-sm font-semibold">{speakerName(n)}</span>
            {n.verified && <BadgeCheck className="size-4 shrink-0 text-primary" />}
          </div>
          <div className="text-xs text-fg-3">{t('people.map.hours', fmtHours(n.total_sec), n.media_count)}</div>
        </div>
        <Button size="icon-sm" variant="ghost" aria-label={t('people.map.clear')} onClick={() => onSelect(null)}>
          <X />
        </Button>
      </div>
      <div className="px-4 pt-3">
        <Button size="sm" variant="secondary" asChild className="w-full">
          <Link to={`/speakers/${n.id}`}>
            {t('people.map.open')} <ArrowUpRight />
          </Link>
        </Button>
      </div>
      <div className="px-4 pb-1 pt-4 text-[12px] font-semibold text-fg-2">{t('people.map.similar')}</div>
      <ul className="px-2 pb-2">
        {list.map(({ j, sim }) => (
          <li key={j}>
            <button
              type="button"
              onClick={() => onSelect(j)}
              className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-surface-2"
            >
              <span className="size-2.5 shrink-0 rounded-full" style={{ background: sexColor(d.nodes[j]) }} />
              <span className="min-w-0 flex-1 truncate">{speakerName(d.nodes[j])}</span>
              <span className="h-1.5 w-14 shrink-0 overflow-hidden rounded-full bg-surface-2">
                <span className="block h-full rounded-full bg-[var(--seq-5)]" style={{ width: `${Math.max(0, sim) * 100}%` }} />
              </span>
              <SimValue v={sim} threshold={threshold} />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function SimValue({ v, threshold }: { v: number; threshold: number }) {
  return (
    <span
      className={cn('w-9 shrink-0 text-right text-[12px] tabular', v >= threshold ? 'font-semibold text-fg' : 'text-fg-2')}
      title={v >= threshold ? t('people.map.sameCluster') : undefined}
    >
      {fmtSim(v)}
    </span>
  )
}
