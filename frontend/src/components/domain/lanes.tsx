/* Timeline lanes: VAD · SNR · diarization · repeated regions · segments, over an arbitrary time domain. */
import { useMemo, useState } from 'react'
import type { ReviewStatus, Timeline } from '@/api/types'
import { fmtClock, fmtTimestamp } from '@/lib/format'
import { reviewStatus } from '@/lib/labels'
import { SERIES } from '@/components/charts/charts'
import { cn } from '@/lib/cn'
import { localized, msgs, t as tr } from '@/i18n'

export const reviewColor: Record<ReviewStatus, string> = {
  unchecked: 'var(--series-muted)',
  passed: 'var(--success)',
  needs_fix: 'var(--warning)',
  excluded: 'var(--danger)',
}

export type LaneKey = 'vad' | 'snr' | 'speaker' | 'repeated' | 'segments'
export const laneLabels: Record<LaneKey, string> = localized(() => {
  const l = msgs().collect.lanes
  return { vad: l.vad, snr: l.snr, speaker: l.speaker, repeated: l.repeated, segments: l.segments }
})
const laneH: Record<LaneKey, number> = { vad: 28, snr: 14, speaker: 14, repeated: 12, segments: 16 }
const GAP = 8

export function lanesHeight(lanes: LaneKey[]) {
  return lanes.reduce((a, l) => a + laneH[l] + GAP, 0) + 18
}

export function LaneLabels({ lanes, className }: { lanes: LaneKey[]; className?: string }) {
  return (
    <div className={cn('shrink-0 select-none text-[11px] text-fg-3', className)}>
      {lanes.map((l) => (
        <div key={l} className="flex items-center" style={{ height: laneH[l], marginBottom: GAP }}>
          {laneLabels[l]}
        </div>
      ))}
    </div>
  )
}

export function Lanes({
  t,
  domain,
  width,
  lanes = ['vad', 'snr', 'speaker', 'repeated', 'segments'],
  activeSegment,
  onSegmentClick,
  playhead,
  onSeek,
  selection,
}: {
  t: Timeline
  domain: [number, number]
  width: number
  lanes?: LaneKey[]
  activeSegment?: string
  onSegmentClick?: (id: string) => void
  playhead?: number | null
  onSeek?: (sec: number) => void
  selection?: [number, number]
}) {
  const [a, b] = domain
  const span = Math.max(1e-6, b - a)
  const X = (s: number) => ((s - a) / span) * width
  const [hover, setHover] = useState<{ x: number; sec: number; text?: string } | null>(null)
  const speakers = useMemo(() => [...new Set(t.diarization.map((d) => d.speaker))].sort(), [t.diarization])
  const spColor = (s: string) => SERIES[speakers.indexOf(s) % SERIES.length]
  const H = lanesHeight(lanes)

  // downsampled frame arrays → only the visible window
  const frames = useMemo(() => {
    const hop = t.hop_sec
    if (!hop) return []
    const i0 = Math.max(0, Math.floor(a / hop) - 1)
    const i1 = Math.min(t.vad.length - 1, Math.ceil(b / hop) + 1)
    const pts: { s: number; v: number; snr: number }[] = []
    const step = Math.max(1, Math.floor((i1 - i0) / Math.max(1, width / 2)))
    for (let i = i0; i <= i1; i += step) pts.push({ s: i * hop, v: t.vad[i], snr: t.snr[i] })
    return pts
  }, [a, b, t, width])

  let y = 0
  const pos: Record<string, number> = {}
  for (const l of lanes) {
    pos[l] = y
    y += laneH[l] + GAP
  }
  const axisY = y
  const tickStep = niceStep(span / Math.max(2, width / 90))
  const ticks: number[] = []
  for (let s = Math.ceil(a / tickStep) * tickStep; s <= b; s += tickStep) ticks.push(s)
  const segs = t.segments.filter((s) => s.end_sec >= a && s.start_sec <= b)

  return (
    <div className="relative" style={{ width, height: H }}>
      <svg
        width={width}
        height={H}
        className="block touch-pan-x"
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const x = e.clientX - r.left
          const sec = a + (x / width) * span
          const seg = segs.find((s) => sec >= s.start_sec && sec <= s.end_sec)
          setHover({ x, sec, text: seg?.text })
        }}
        onMouseLeave={() => setHover(null)}
        onClick={(e) => {
          if (!onSeek) return
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          onSeek(a + ((e.clientX - r.left) / width) * span)
        }}
      >
        {selection && <rect x={X(selection[0])} y={0} width={Math.max(1, X(selection[1]) - X(selection[0]))} height={axisY} fill="var(--primary)" opacity={0.08} />}
        {lanes.includes('vad') && (
          <g transform={`translate(0 ${pos.vad})`}>
            <rect width={width} height={laneH.vad} rx={4} fill="var(--surface-2)" />
            <path
              d={`M0,${laneH.vad} ${frames.map((p) => `L${X(p.s).toFixed(1)},${(laneH.vad - p.v * (laneH.vad - 2)).toFixed(1)}`).join(' ')} L${width},${laneH.vad} Z`}
              fill="var(--series-1)"
              opacity={0.35}
            />
            <path d={frames.map((p, i) => `${i ? 'L' : 'M'}${X(p.s).toFixed(1)},${(laneH.vad - p.v * (laneH.vad - 2)).toFixed(1)}`).join(' ')} fill="none" stroke="var(--series-1)" strokeWidth={1.25} />
          </g>
        )}
        {lanes.includes('snr') && (
          <g transform={`translate(0 ${pos.snr})`}>
            <rect width={width} height={laneH.snr} rx={3} fill="var(--surface-2)" />
            {frames.map((p, i) => {
              const next = frames[i + 1]?.s ?? b
              const k = Math.max(0, Math.min(1, p.snr / 35))
              return <rect key={i} x={X(p.s)} width={Math.max(0.5, X(next) - X(p.s))} y={1} height={laneH.snr - 2} fill={`color-mix(in oklab, var(--seq-7) ${Math.round(k * 100)}%, var(--seq-1))`} opacity={0.25 + k * 0.75} />
            })}
          </g>
        )}
        {lanes.includes('speaker') && (
          <g transform={`translate(0 ${pos.speaker})`}>
            <rect width={width} height={laneH.speaker} rx={3} fill="var(--surface-2)" />
            {t.diarization
              .filter((d) => d.end >= a && d.start <= b)
              .map((d, i) => (
                <rect key={i} x={X(d.start)} width={Math.max(1, X(d.end) - X(d.start) - 1)} y={d.overlap ? 0 : 2} height={d.overlap ? laneH.speaker : laneH.speaker - 4} rx={2} fill={spColor(d.speaker)} opacity={d.overlap ? 1 : 0.85} />
              ))}
          </g>
        )}
        {lanes.includes('repeated') && (
          <g transform={`translate(0 ${pos.repeated})`}>
            <defs>
              <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" fill="var(--warning-soft)" />
                <line x1="0" y1="0" x2="0" y2="6" stroke="var(--warning)" strokeWidth="2" />
              </pattern>
            </defs>
            <rect width={width} height={laneH.repeated} rx={3} fill="var(--surface-2)" />
            {t.repeated
              .filter((r) => r.end >= a && r.start <= b)
              .map((r) => (
                <rect key={r.id} x={X(r.start)} width={Math.max(2, X(r.end) - X(r.start))} height={laneH.repeated} rx={2} fill={r.excluded ? 'url(#hatch)' : 'var(--surface-3)'} stroke={r.excluded ? 'var(--warning)' : 'var(--border-strong)'} strokeWidth={1} />
              ))}
          </g>
        )}
        {lanes.includes('segments') && (
          <g transform={`translate(0 ${pos.segments})`}>
            {segs.map((s) => {
              const active = s.id === activeSegment
              return (
                <rect
                  key={s.id}
                  x={X(s.start_sec)}
                  width={Math.max(2, X(s.end_sec) - X(s.start_sec) - 1.5)}
                  y={active ? 0 : 2}
                  height={active ? laneH.segments : laneH.segments - 4}
                  rx={3}
                  fill={reviewColor[s.review_status]}
                  stroke={active ? 'var(--fg)' : 'none'}
                  strokeWidth={active ? 1.5 : 0}
                  className={onSegmentClick ? 'cursor-pointer hover:opacity-80' : undefined}
                  onClick={(e) => {
                    if (!onSegmentClick) return
                    e.stopPropagation()
                    onSegmentClick(s.id)
                  }}
                />
              )
            })}
          </g>
        )}
        <g transform={`translate(0 ${axisY})`}>
          {ticks.map((s) => (
            <g key={s}>
              <line x1={X(s)} x2={X(s)} y1={-4} y2={0} stroke="var(--chart-axis)" />
              <text x={X(s)} y={12} textAnchor={X(s) < 20 ? 'start' : X(s) > width - 20 ? 'end' : 'middle'} className="fill-fg-3 text-[10px] tabular">
                {span < 60 ? `${fmtTimestamp(s).replace(/\.\d+$/, '')}${tickStep < 1 ? '.' + String(Math.round((s % 1) * 10)) : ''}` : fmtClock(s)}
              </text>
            </g>
          ))}
        </g>
        {playhead != null && playhead >= a && playhead <= b && <line x1={X(playhead)} x2={X(playhead)} y1={0} y2={axisY} stroke="var(--danger)" strokeWidth={1.5} />}
        {hover && <line x1={hover.x} x2={hover.x} y1={0} y2={axisY} stroke="var(--fg-3)" strokeWidth={1} opacity={0.6} />}
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute top-0 z-10 max-w-64 -translate-x-1/2 -translate-y-full rounded-md border border-border bg-surface px-2 py-1 text-[11px] shadow-md"
          style={{ left: Math.min(Math.max(hover.x, 80), width - 80) }}
        >
          <div className="font-mono tabular text-fg-2">{fmtTimestamp(hover.sec)}</div>
          {hover.text && <div className="line-clamp-2 text-fg">{hover.text}</div>}
        </div>
      )}
    </div>
  )
}

function niceStep(raw: number) {
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
  return steps.find((s) => s >= raw) ?? 3600
}

export function SpeakerLegend({ t }: { t: Timeline }) {
  const speakers = [...new Set(t.diarization.map((d) => d.speaker))].sort()
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-fg-3">
      {speakers.map((s, i) => (
        <span key={s} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: SERIES[i % SERIES.length] }} />
          <span className="font-mono">{s}</span>
        </span>
      ))}
      {(['passed', 'needs_fix', 'excluded', 'unchecked'] as ReviewStatus[]).map((r) => (
        <span key={r} className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm" style={{ background: reviewColor[r] }} />
          {reviewStatus[r].label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-4 rounded-sm border border-warning" style={{ background: 'repeating-linear-gradient(135deg, var(--warning) 0 2px, var(--warning-soft) 2px 5px)' }} />
        {tr('collect.lanes.excludedRepeated')}
      </span>
    </div>
  )
}
