import { useCallback, useEffect, useRef, useState } from 'react'
import { audioUrl } from '@/api/client'

type Status = 'idle' | 'loading' | 'playing' | 'paused'
export interface SentenceRef {
  id: string
  start_sec: number
  revision: number
}

/** Plays exactly one segment at a time (the server cuts [start, end] with no padding), so playback stops at the
 * sentence end by itself. `clock()` returns the absolute media time of the playhead for karaoke highlighting. */
export function useSentencePlayer() {
  const el = useRef<HTMLAudioElement | null>(null)
  const offset = useRef(0)
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

  useEffect(() => () => el.current?.pause(), [])

  const play = useCallback(
    (seg: SentenceRef) => {
      const a = audio()
      const s = cur.current
      frozen.current = null
      if (s.id === seg.id && s.status !== 'idle') {
        if (s.status === 'playing' || s.status === 'loading') {
          a.pause()
          setState({ id: seg.id, status: 'paused' })
        } else {
          void a.play().catch(() => setState({ id: null, status: 'idle' }))
        }
        return
      }
      a.pause()
      a.src = audioUrl.segment(seg.id, 0, seg.revision)
      offset.current = seg.start_sec
      setState({ id: seg.id, status: 'loading' })
      void a.play().catch((e: unknown) => {
        // a newer play() interrupting this one is not an error
        if (e instanceof DOMException && e.name === 'AbortError') return
        // autoplay refused: keep the sentence selected so the next click plays it
        setState((x) => (x.id === seg.id ? { id: seg.id, status: 'paused' } : x))
      })
    },
    [audio],
  )
  const stop = useCallback(() => {
    frozen.current = null
    el.current?.pause()
    setState({ id: null, status: 'idle' })
  }, [])
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
        frozen.current = offset.current + (Number.isFinite(a.duration) ? a.duration : 0) * frac
        setState({ id, status: 'paused' })
        return true
      },
    }
    return () => {
      delete w.__votiPlayer
    }
  }, [])

  return { ...state, play, stop, clock }
}
