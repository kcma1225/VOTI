import { useCallback, useEffect, useRef, useState } from 'react'
import { audioUrl } from '@/api/client'

type Status = 'idle' | 'loading' | 'playing' | 'paused'
export interface SentenceRef {
  id: string
  start_sec: number
  end_sec?: number
  revision: number
}

/** Plays exactly one segment at a time (the server cuts [start, end] with no padding), so playback stops at the
 * sentence end by itself. `clock()` returns the absolute media time of the playhead for karaoke highlighting.
 *
 * `play(seg)` toggles: start / pause / resume. `play(seg, at)` (absolute media seconds, e.g. a clicked character)
 * always plays from `at` to the sentence end: a seek when that point is already buffered in the loaded clip,
 * otherwise a clip requested from `at` (`?from=`; the clip endpoint has no Range support, so an unbuffered seek
 * would not stick). */
export function useSentencePlayer() {
  const el = useRef<HTMLAudioElement | null>(null)
  const offset = useRef(0)
  /** playRange(): the media-time window being played from the full media audio (null = a sentence clip) */
  const range = useRef<{ start: number; end: number; raf: number } | null>(null)
  /** mock screenshots only: a fixed playhead (headless audio can't be relied on to play or seek) */
  const frozen = useRef<number | null>(null)
  const [state, setState] = useState<{ id: string | null; status: Status }>({ id: null, status: 'idle' })
  const cur = useRef(state)
  cur.current = state

  const audio = useCallback(() => {
    if (!el.current) {
      const a = new Audio()
      a.preload = 'auto'
      a.onended = () => setState({ id: null, status: 'idle' })
      a.onplaying = () => setState((s) => (s.id ? { id: s.id, status: 'playing' } : s))
      a.onerror = () => setState({ id: null, status: 'idle' })
      el.current = a
    }
    return el.current
  }, [])

  useEffect(
    () => () => {
      el.current?.pause()
      if (range.current) cancelAnimationFrame(range.current.raf)
    },
    [],
  )
  /** leave range mode (a sentence clip or stop follows) */
  const endRange = useCallback(() => {
    if (range.current) cancelAnimationFrame(range.current.raf)
    range.current = null
  }, [])

  const play = useCallback(
    (seg: SentenceRef, at?: number) => {
      const a = audio()
      let s = cur.current
      frozen.current = null
      if (range.current) {
        // leaving a media time range: the next clip loads from scratch
        endRange()
        a.pause()
        s = { id: null, status: 'idle' }
      }
      const failed = (e: unknown) => {
        // a newer play() interrupting this one is not an error
        if (e instanceof DOMException && e.name === 'AbortError') return
        // autoplay refused: keep the sentence selected so the next click plays it
        setState((x) => (x.id === seg.id ? { id: seg.id, status: 'paused' } : x))
      }
      const load = (from: number | null) => {
        a.pause()
        a.src = audioUrl.segment(seg.id, 0, seg.revision, from ?? undefined)
        offset.current = from ?? seg.start_sec
        setState({ id: seg.id, status: 'loading' })
        void a.play().catch(failed)
      }
      // a clicked character: clamp into the sentence (the server clamps the same way)
      const from = at == null ? null : Math.max(seg.start_sec, seg.end_sec != null ? Math.min(at, seg.end_sec - 0.05) : at)
      if (s.id === seg.id && s.status !== 'idle') {
        if (from != null) {
          const rel = from - offset.current
          const b = a.buffered
          let inBuffer = false
          for (let i = 0; i < b.length; i++) if (rel >= b.start(i) && rel <= b.end(i) - 0.05) inBuffer = true
          if (rel >= 0 && inBuffer) {
            a.currentTime = rel
            if (a.paused) {
              setState({ id: seg.id, status: 'playing' })
              void a.play().catch(failed)
            }
          } else load(from > seg.start_sec + 0.01 ? from : null)
          return
        }
        if (s.status === 'playing' || s.status === 'loading') {
          a.pause()
          setState({ id: seg.id, status: 'paused' })
        } else {
          void a.play().catch(() => setState({ id: null, status: 'idle' }))
        }
        return
      }
      load(from != null && from > seg.start_sec + 0.01 ? from : null)
    },
    [audio, endRange],
  )
  /** Play [start, end] (absolute media seconds) of the full media audio under `key` (e.g. an OCR event), for time
   * ranges that are not a segment. Toggles like play(); clock() stays absolute media time. */
  const playRange = useCallback(
    (key: string, mediaId: string, start: number, end: number) => {
      const a = audio()
      const s = cur.current
      frozen.current = null
      if (s.id === key && s.status !== 'idle' && range.current) {
        if (s.status === 'playing' || s.status === 'loading') {
          a.pause()
          setState({ id: key, status: 'paused' })
        } else {
          if (a.currentTime >= range.current.end - 0.05) a.currentTime = range.current.start
          void a.play().catch(() => setState({ id: null, status: 'idle' }))
        }
        return
      }
      endRange()
      a.pause()
      offset.current = 0
      const src = audioUrl.media(mediaId)
      if (!a.src.endsWith(src)) a.src = src
      const r = { start, end, raf: 0 }
      range.current = r
      setState({ id: key, status: 'loading' })
      const stopAtEnd = () => {
        if (range.current !== r) return
        if (!a.paused && a.currentTime >= r.end) {
          a.pause()
          range.current = null
          setState({ id: null, status: 'idle' })
          return
        }
        r.raf = requestAnimationFrame(stopAtEnd)
      }
      const go = () => {
        if (range.current !== r) return
        a.currentTime = start
        void a
          .play()
          .then(() => {
            if (range.current === r) r.raf = requestAnimationFrame(stopAtEnd)
          })
          .catch((e) => {
            if (e instanceof DOMException && e.name === 'AbortError') return
            setState((x) => (x.id === key ? { id: key, status: 'paused' } : x))
          })
      }
      if (a.readyState >= 1) go()
      else a.addEventListener('loadedmetadata', go, { once: true })
    },
    [audio, endRange],
  )
  const stop = useCallback(() => {
    frozen.current = null
    endRange()
    el.current?.pause()
    setState({ id: null, status: 'idle' })
  }, [endRange])
  const clock = useCallback(() => (frozen.current != null ? frozen.current : el.current ? offset.current + el.current.currentTime : null), [])

  // Screenshot hook in mock builds: freeze the active sentence at a fraction of its length.
  useEffect(() => {
    if (import.meta.env.VITE_MOCK !== '1') return
    const w = window as unknown as { __votiPlayer?: unknown }
    w.__votiPlayer = {
      freeze: async (frac: number) => {
        const a = el.current
        const id = cur.current.id
        if (!a || !id) return false
        if (a.readyState < 1) await new Promise((r) => a.addEventListener('loadedmetadata', r, { once: true }))
        a.pause()
        const rg = range.current
        frozen.current = rg ? rg.start + (rg.end - rg.start) * frac : offset.current + (Number.isFinite(a.duration) ? a.duration : 0) * frac
        setState({ id, status: 'paused' })
        return true
      },
    }
    return () => {
      delete w.__votiPlayer
    }
  }, [])

  return { ...state, play, playRange, stop, clock }
}
