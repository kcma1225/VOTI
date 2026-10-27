/* Character-level ("karaoke") highlighting of a sentence while its audio plays.
 *
 * Units come from SegmentDetail.words ({t, s, e}) or GET /media/{id}/words ([t, s, e]); they concatenate exactly to
 * the segment text and carry absolute media times (null = untimed, e.g. punctuation). Spoken units keep the normal
 * text colour, the current unit gets a pill in the speaker colour (primary without one) (background + box-shadow only, so nothing reflows), upcoming
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

/** Start time of the unit that holds character `charIdx` of the text (units concatenate exactly to it). An untimed
 * unit (punctuation) seeks to the next timed one, the tail to the last one; null when nothing is timed. */
export function timeAtChar(units: Unit[], charIdx: number): number | null {
  let pos = 0
  let hit = units.length - 1
  for (let i = 0; i < units.length; i++) {
    const len = units[i].t.length
    if (charIdx < pos + len) {
      hit = i
      break
    }
    pos += len
  }
  for (let i = hit; i < units.length; i++) if (units[i].s != null) return units[i].s
  for (let i = hit; i >= 0; i--) if (units[i].s != null) return units[i].s
  return null
}

/** Pure renderer. `time` null = not playing: plain text. With `onSeek`, every timed unit is clickable (tap on touch)
 * and seeks there; `focusable` also makes them keyboard stops (Enter/Space) — only for a single sentence (reviewer),
 * never for long lists. The current unit's pill takes the speaker colour (--spk-pill/--spk-on, see speakerVars). */
export function KaraokeText({
  units,
  time,
  onSeek,
  focusable,
  seekLabel,
  className,
}: {
  units: Unit[]
  time: number | null
  onSeek?: (t: number) => void
  focusable?: boolean
  /** accessible name prefix for focusable units, e.g. "Play from" */
  seekLabel?: string
  className?: string
}) {
  const states = time == null ? null : unitStates(units, time)
  return (
    <span className={className}>
      {units.map((u, i) => {
        const st = states?.[i]
        const seekable = !!onSeek && u.s != null
        const seek = seekable ? () => onSeek!(u.s!) : undefined
        return (
          <span
            key={i}
            data-k={st}
            data-seek={seekable || undefined}
            role={seekable && focusable ? 'button' : undefined}
            tabIndex={seekable && focusable ? 0 : undefined}
            aria-label={seekable && focusable && seekLabel ? `${seekLabel} ${u.t}` : undefined}
            onClick={seek ? (e) => (e.stopPropagation(), e.preventDefault(), seek()) : undefined}
            onKeyDown={
              seek && focusable
                ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      e.stopPropagation()
                      seek()
                    }
                  }
                : undefined
            }
            className={cn(
              'rounded-[3px] transition-[color,background-color,box-shadow] duration-75',
              st === 'upcoming' && 'text-fg-3',
              st === 'current' && 'hl-current',
              seekable && 'hl-seek',
            )}
          >
            {u.t}
          </span>
        )
      })}
    </span>
  )
}

/** Plain sentence text whose characters are clickable before any timing is known (review list rows): a click
 * reports the character index; the caller resolves the time (word timings fetched on demand). */
export function SeekableChars({ text, onChar, className }: { text: string; onChar: (charIdx: number) => void; className?: string }) {
  const chars = Array.from(text)
  let pos = 0
  return (
    <span className={className}>
      {chars.map((c, i) => {
        const at = pos
        pos += c.length
        if (/\s/.test(c)) return <span key={i}>{c}</span>
        return (
          <span key={i} data-seek className="hl-seek" onClick={(e) => (e.stopPropagation(), e.preventDefault(), onChar(at))}>
            {c}
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
