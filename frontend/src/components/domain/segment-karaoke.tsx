/* Karaoke for one row of a segment list played with useSentencePlayer (review list, speaker samples): the playing
 * row's word timings are fetched for that row only; a click on a character of an idle row plays from there. */
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { qk, useSegment } from '@/api/queries'
import type { SegmentSummary } from '@/api/types'
import type { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { KaraokeText, timeAtChar, toUnits, useClock } from '@/components/domain/karaoke'

export type SentencePlayer = ReturnType<typeof useSentencePlayer>

/** The playing row's text: per-character karaoke from the segment's word timings, or a sentence-level highlight
 * when the segment has no alignment. Characters seek (click / tap). */
export function PlayingText({ s, player }: { s: SegmentSummary; player: SentencePlayer }) {
  const detail = useSegment(s.id)
  const units = toUnits(detail.data?.words ?? null, s.start_sec, s.end_sec)
  const time = useClock(player.clock, true)
  if (units) return <KaraokeText units={units} time={time} onSeek={(at) => player.play(s, at)} />
  return <span className="hl-sentence rounded-[3px]">{s.text}</span>
}

/** Plays `s` from character `charIdx` (fetches its word timings; from the sentence start without them). */
export function usePlayFromChar(player: SentencePlayer) {
  const qc = useQueryClient()
  return async (s: SegmentSummary, charIdx: number) => {
    let at: number | undefined
    try {
      const d = await qc.fetchQuery({ queryKey: qk.segment(s.id), queryFn: () => api.segment(s.id), staleTime: 60_000 })
      const units = toUnits(d.words ?? null, s.start_sec, s.end_sec)
      at = units ? (timeAtChar(units, charIdx) ?? undefined) : undefined
    } catch {
      /* no timings: play the sentence from its start */
    }
    player.play(s, at)
  }
}
