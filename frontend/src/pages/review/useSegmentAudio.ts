import { t } from '@/i18n'
import { useCallback, useEffect, useRef, useState } from 'react'
import { audioUrl } from '@/api/client'

export const PAD = 1.0

/** Loads the padded segment WAV, decodes peaks for the waveform, and plays sub-ranges of it. */
export function useSegmentAudio(segmentId: string | undefined, revision: number | undefined) {
  const [peaks, setPeaks] = useState<Float32Array | null>(null)
  const [duration, setDuration] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0) // seconds within the padded clip
  /** absolute media time of the clip's first sample (server clamps the padding at 0 s and at the media end) */
  const [clipStart, setClipStart] = useState<number | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const stopAt = useRef<number | null>(null)
  const raf = useRef(0)
  const rate = useRef(1)

  useEffect(() => {
    if (!segmentId) return
    let alive = true
    let url: string | null = null
    setPeaks(null)
    setError(null)
    setPlaying(false)
    setTime(0)
    setClipStart(null)
    ;(async () => {
      try {
        const res = await fetch(audioUrl.segment(segmentId, PAD, revision), { credentials: 'same-origin' })
        if (!res.ok) throw new Error(t('process.audio.loadFailedStatus', res.status))
        const cs = Number.parseFloat(res.headers.get('X-Clip-Start') ?? '')
        if (alive && Number.isFinite(cs)) setClipStart(cs)
        const buf = await res.arrayBuffer()
        if (!alive) return
        url = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }))
        const el = new Audio(url)
        el.preload = 'auto'
        el.playbackRate = rate.current
        audio.current?.pause()
        audio.current = el
        el.onended = () => setPlaying(false)
        try {
          const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
          const ctx = new Ctx()
          const decoded = await ctx.decodeAudioData(buf.slice(0))
          void ctx.close()
          if (!alive) return
          const ch = decoded.getChannelData(0)
          const N = 1200
          const block = Math.max(1, Math.floor(ch.length / N))
          const p = new Float32Array(N * 2)
          for (let i = 0; i < N; i++) {
            let mn = 1
            let mx = -1
            for (let j = i * block; j < Math.min(ch.length, (i + 1) * block); j++) {
              const v = ch[j]
              if (v < mn) mn = v
              if (v > mx) mx = v
            }
            p[i * 2] = mn
            p[i * 2 + 1] = mx
          }
          setPeaks(p)
          setDuration(decoded.duration)
        } catch {
          setError(t('process.audio.decodeFailed'))
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : t('process.audio.loadFailed'))
      }
    })()
    return () => {
      alive = false
      audio.current?.pause()
      cancelAnimationFrame(raf.current)
      if (url) URL.revokeObjectURL(url)
    }
  }, [segmentId, revision])

  const loop = useCallback(() => {
    const el = audio.current
    if (!el) return
    setTime(el.currentTime)
    if (stopAt.current !== null && el.currentTime >= stopAt.current) {
      el.pause()
      setPlaying(false)
      return
    }
    if (!el.paused) raf.current = requestAnimationFrame(loop)
  }, [])

  const play = useCallback(
    (from: number, to: number | null) => {
      const el = audio.current
      if (!el) return
      el.currentTime = Math.max(0, from)
      stopAt.current = to
      void el.play().then(
        () => {
          setPlaying(true)
          cancelAnimationFrame(raf.current)
          raf.current = requestAnimationFrame(loop)
        },
        () => setPlaying(false),
      )
    },
    [loop],
  )
  const pause = useCallback(() => {
    audio.current?.pause()
    setPlaying(false)
  }, [])
  const seek = useCallback((t: number) => {
    if (audio.current) audio.current.currentTime = t
    setTime(t)
  }, [])
  // Screenshot hook in mock builds: freeze playback at a fraction of the (unpadded) segment.
  useEffect(() => {
    if (import.meta.env.VITE_MOCK !== '1') return
    const w = window as unknown as { __votiSegAudio?: unknown }
    w.__votiSegAudio = {
      freeze: (frac: number) => {
        const el = audio.current
        if (!el || !duration) return false
        el.pause()
        const at = PAD + Math.max(0, duration - 2 * PAD) * frac
        el.currentTime = at
        setTime(at)
        setPlaying(false)
        return true
      },
    }
    return () => {
      delete w.__votiSegAudio
    }
  }, [duration])

  const setRate = useCallback((r: number) => {
    rate.current = r
    if (audio.current) audio.current.playbackRate = r
  }, [])

  return { peaks, duration, error, playing, time, clipStart, play, pause, seek, setRate, ready: !!audio.current && !!peaks }
}
