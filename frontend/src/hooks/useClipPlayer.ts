import { useCallback, useEffect, useRef, useState } from 'react'
import { audioUrl } from '@/api/client'

/** One shared <audio> for lists of segment samples: play(id) toggles; only one plays at a time. */
export function useClipPlayer() {
  const el = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)
  useEffect(() => () => el.current?.pause(), [])
  const toggle = useCallback(
    (id: string) => {
      if (!el.current) {
        el.current = new Audio()
        el.current.onended = () => setPlaying(null)
        el.current.onpause = () => setPlaying(null)
        el.current.onplaying = () => setLoading(null)
      }
      const a = el.current
      if (playing === id) {
        a.pause()
        return
      }
      a.pause()
      a.src = audioUrl.segment(id, 0.15)
      setLoading(id)
      a.play().then(
        () => setPlaying(id),
        () => {
          setLoading(null)
          setPlaying(null)
        },
      )
    },
    [playing],
  )
  return { playing, loading, toggle }
}
