/* Character-level ("karaoke") highlighting of a sentence while its audio plays.
 *
 * Units come from SegmentDetail.words ({t, s, e}) or GET /media/{id}/words ([t, s, e]); they concatenate exactly to
 * the segment text and carry absolute media times (null = untimed, e.g. punctuation). Spoken units keep the normal
 * text colour, the current unit gets a primary pill (background + box-shadow only, so nothing reflows), upcoming
 * units are muted. Without units the caller falls back to a sentence-level highlight. */
import { useEffect, useRef, useState } from 'react'
import type { MediaWords, WordUnit } from '@/api/types'
import { cn } from '@/lib/cn'

export type Unit = WordUnit
type UnitState = 'spoken' | 'current' | 'upcoming'

/** Normalises both API shapes; returns null when there is nothing timed to highlight. */
export function toUnits(words: WordUnit[] | MediaWords[string] | null | undefined, start?: number, end?: number): Unit[] | null {
  if (!words?.length) return null
  const units: Unit[] = words.map((w) => (Array.isArray(w) ? { t: w[0], s: w[1], e: w[2] } : w))
  const timed = units.filter((u) => u.s != null)
  if (!timed.length) return null
  // the API always returns absolute media seconds (no guessing a relative time base)
  void start
  void end
  return units
}

export function unitStates(units: Unit[], time: number): UnitState[] {
  const out: UnitState[] = new Array(units.length)
  let prev: UnitState | null = null
  const pending: number[] = []
  units.forEach((u, i) => {
    if (u.s == null) {
      if (prev) out[i] = prev === 'upcoming' ? 'upcoming' : 'spoken'
      else pending.push(i)
      return
    }
    const e = u.e ?? u.s
    const st: UnitState = time >= e ? 'spoken' : time >= u.s ? 'current' : 'upcoming'
    out[i] = st
    // leading untimed units follow the first timed one
    if (!prev) pending.splice(0).forEach((k) => (out[k] = st === 'upcoming' ? 'upcoming' : 'spoken'))
    prev = st
  })
  pending.forEach((k) => (out[k] = 'upcoming'))
  return out
}

/** Pure renderer. `time` null = not playing: plain text. */
export function KaraokeText({
  units,
  time,
  onSeek,
  className,
}: {
  units: Unit[]
  time: number | null
  onSeek?: (t: number) => void
  className?: string
}) {
  const states = time == null ? null : unitStates(units, time)
  return (
    <span className={className}>
      {units.map((u, i) => {
        const st = states?.[i]
        const seekable = !!onSeek && u.s != null
        return (
          <span
            key={i}
            data-k={st}
            onClick={seekable ? (e) => (e.stopPropagation(), onSeek!(u.s!)) : undefined}
            className={cn(
              'rounded-[3px] transition-[color,background-color,box-shadow] duration-75',
              st === 'upcoming' && 'text-fg-3',
              st === 'current' && 'bg-primary text-primary-fg shadow-[0_0_0_2px_var(--primary)]',
              seekable && 'cursor-pointer hover:bg-primary-soft hover:shadow-[0_0_0_2px_var(--primary-soft)]',
              seekable && st === 'current' && 'hover:bg-primary hover:shadow-[0_0_0_2px_var(--primary)]',
            )}
          >
            {u.t}
          </span>
        )
      })}
    </span>
  )
}

/** Polls `clock()` every animation frame while `active`; returns the latest time (null when inactive). */
export function useClock(clock: () => number | null, active: boolean) {
  const [time, setTime] = useState<number | null>(null)
  const fn = useRef(clock)
  fn.current = clock
  useEffect(() => {
    if (!active) {
      setTime(null)
      return
    }
    let raf = 0
    const loop = () => {
      const v = fn.current()
      setTime((p) => (p !== null && v !== null && Math.abs(p - v) < 0.008 ? p : v))
      raf = requestAnimationFrame(loop)
    }
    loop()
    return () => cancelAnimationFrame(raf)
  }, [active])
  return time
}
