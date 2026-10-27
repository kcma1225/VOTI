import { useEffect, useRef } from 'react'
import { fmtTimestamp } from '@/lib/format'
import { cn } from '@/lib/cn'

/** Canvas waveform of the padded clip; [start,end] region is highlighted and has draggable handles. */
export function Waveform({
  peaks,
  duration,
  width,
  height = 112,
  region,
  onRegionChange,
  playhead,
  onSeek,
  clipStart,
}: {
  peaks: Float32Array | null
  duration: number
  width: number
  height?: number
  region: [number, number] // seconds within clip
  onRegionChange: (r: [number, number]) => void
  playhead: number
  onSeek: (t: number) => void
  clipStart: number // absolute time of clip t=0 (for labels)
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<null | 'start' | 'end'>(null)
  const X = (t: number) => (duration ? (t / duration) * width : 0)
  const T = (x: number) => Math.max(0, Math.min(duration, (x / width) * duration))

  useEffect(() => {
    const c = canvas.current
    if (!c || !width) return
    const dpr = window.devicePixelRatio || 1
    c.width = Math.round(width * dpr)
    c.height = Math.round(height * dpr)
    const g = c.getContext('2d')!
    g.scale(dpr, dpr)
    g.clearRect(0, 0, width, height)
    const cs = getComputedStyle(document.documentElement)
    const inC = cs.getPropertyValue('--primary').trim() || '#3b5bdb'
    const outC = cs.getPropertyValue('--border-strong').trim() || '#ccc'
    const mid = height / 2
    g.fillStyle = cs.getPropertyValue('--chart-grid').trim()
    g.fillRect(0, mid, width, 1)
    if (!peaks) return
    const n = peaks.length / 2
    const bar = 2
    const gap = 1
    const cols = Math.floor(width / (bar + gap))
    for (let i = 0; i < cols; i++) {
      const a = Math.floor((i / cols) * n)
      const b = Math.max(a + 1, Math.floor(((i + 1) / cols) * n))
      let mx = 0
      for (let j = a; j < b; j++) mx = Math.max(mx, Math.abs(peaks[j * 2]), Math.abs(peaks[j * 2 + 1]))
      const h = Math.max(1.5, Math.min(1, mx * 1.6) * (height - 8))
      const x = i * (bar + gap)
      const t = (x / width) * duration
      g.fillStyle = t >= region[0] && t <= region[1] ? inC : outC
      g.beginPath()
      g.roundRect(x, mid - h / 2, bar, h, 1)
      g.fill()
    }
  }, [peaks, width, height, region, duration])

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const t = T(e.clientX - r.left)
    if (drag.current === 'start') onRegionChange([Math.min(t, region[1] - 0.2), region[1]])
    else onRegionChange([region[0], Math.max(t, region[0] + 0.2)])
  }

  return (
    <div
      className="relative select-none touch-none"
      style={{ width, height }}
      onPointerMove={onPointerMove}
      onPointerUp={() => (drag.current = null)}
      onPointerLeave={() => (drag.current = null)}
      onClick={(e) => {
        if (drag.current) return
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
        onSeek(T(e.clientX - r.left))
      }}
    >
      <div className="absolute inset-y-0 rounded-md bg-primary/[0.07]" style={{ left: X(region[0]), width: Math.max(0, X(region[1]) - X(region[0])) }} />
      <canvas ref={canvas} style={{ width, height }} className="relative block" aria-label="波形" role="img" />
      {(['start', 'end'] as const).map((k) => {
        const x = X(k === 'start' ? region[0] : region[1])
        return (
          <div
            key={k}
            role="slider"
            aria-label={k === 'start' ? '起點' : '終點'}
            aria-valuenow={k === 'start' ? region[0] : region[1]}
            tabIndex={-1}
            onPointerDown={(e) => {
              e.stopPropagation()
              drag.current = k
              ;(e.currentTarget.parentElement as HTMLElement).setPointerCapture?.(e.pointerId)
            }}
            onClick={(e) => e.stopPropagation()}
            className="group absolute inset-y-0 z-10 flex w-6 -translate-x-1/2 cursor-ew-resize justify-center"
            style={{ left: x }}
          >
            <span className="h-full w-0.5 bg-primary" />
            <span className={cn('absolute top-1/2 h-8 w-2.5 -translate-y-1/2 rounded-full border-2 border-primary bg-surface shadow-sm transition-transform group-hover:scale-110')} />
            <span className={cn('absolute -top-5 whitespace-nowrap rounded bg-primary px-1 font-mono text-[10px] leading-4 text-primary-fg', k === 'start' ? 'left-1/2' : 'right-1/2')}>
              {fmtTimestamp(clipStart + (k === 'start' ? region[0] : region[1]))}
            </span>
          </div>
        )
      })}
      <div className="pointer-events-none absolute inset-y-0 z-20 w-px bg-danger" style={{ left: X(playhead) }} />
    </div>
  )
}
