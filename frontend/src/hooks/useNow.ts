import { useEffect, useState } from 'react'

/** Wall-clock milliseconds, re-rendering every `ms` while `enabled` (for countdowns). */
export function useNow(ms = 1000, enabled = true) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms, enabled])
  return now
}

/** "m:ss" (or "h:mm:ss") for a positive number of seconds. */
export function fmtCountdown(sec: number) {
  const s = Math.max(0, Math.ceil(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`
}
