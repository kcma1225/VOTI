/* Media page: the diarized speakers of this video (SPEAKER_00 …), one card each in its speaker colour (the same
 * colour as the timeline lane and the transcript highlights), with speaking time, segment count and Vox-Profile
 * estimates aggregated over that speaker's valid segments. A card opens the full distributions, representative
 * segments to play and the cross-video candidate speaker (cluster). */
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { ArrowUpRight, Info, Loader2, Pause, Play, ShieldX, Users } from 'lucide-react'
import { useMediaSegments, useMediaSpeakers, useMediaWords } from '@/api/queries'
import type { Dist, MediaSpeaker, SegmentSummary } from '@/api/types'
import { fmtClock, fmtSec, fmtTimestamp } from '@/lib/format'
import { accentBroadLabel, ageGroupLabel, emotionLabel, sexLabel, speechFlowLabel, traitLabel, voiceQualityLabel } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'
import { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { Dialog } from '@/components/ui/overlay'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { DistBars, speakerName, topOf } from '@/components/domain/speakers'
import { KaraokeText, toUnits, useClock } from '@/components/domain/karaoke'
import { localSpeakerName, speakerColor, speakerVars } from '@/components/domain/speaker-color'

type Player = ReturnType<typeof useSentencePlayer>

const pct = (p: number | null | undefined) => (p == null ? null : Math.round(p * 100))

/** Top entries of a {label: p} map, highest first. */
const tops = (d: Dist | undefined, n: number) =>
  Object.entries(d ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)

function Trait({ label, value, sub }: { label: string; value: string; sub?: string | null }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] text-fg-3">{label}</div>
      <div className="truncate text-[13px] font-medium leading-5">
        {value}
        {sub && <span className="ml-1 text-[11px] font-normal text-fg-3">{sub}</span>}
      </div>
    </div>
  )
}

function SpeakerCard({ sp, share, onOpen }: { sp: MediaSpeaker; share: number; onOpen: () => void }) {
  const t = useT()
  const name = localSpeakerName(sp.color_index)
  const p = sp.profile
  const emo = topOf(p.emotion)
  const flow = topOf(p.speech_flow)
  const vq = tops(p.voice_quality, 3)
  const has = sp.profiled_segments > 0
  return (
    <button
      type="button"
      onClick={onOpen}
      data-speaker-card={sp.local_speaker}
      aria-label={t('people.mediaSpeakers.showDetails', name)}
      style={speakerVars(sp.color_index)}
      className="group relative flex min-w-0 flex-col gap-3 overflow-hidden rounded-xl border border-border bg-surface p-3.5 pl-4 text-left transition-colors hover:border-border-strong hover:bg-surface-2/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: speakerColor(sp.color_index) }} />
      <div className="flex min-w-0 items-center gap-2">
        <span aria-hidden className="size-3 shrink-0 rounded-full" style={{ background: speakerColor(sp.color_index) }} />
        <span className="truncate text-sm font-semibold">{name}</span>
        <span className="shrink-0 font-mono text-[11px] text-fg-3">{sp.local_speaker}</span>
        <span className="ml-auto shrink-0 text-[13px] font-semibold tabular">{fmtClock(sp.total_sec)}</span>
      </div>
      <div>
        <div className="h-1.5 rounded-full bg-surface-3">
          <div className="h-full rounded-full" style={{ width: `${Math.max(share > 0 ? 2 : 0, share * 100)}%`, background: speakerColor(sp.color_index) }} />
        </div>
        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 text-[11px] text-fg-3">
          <span className="tabular">{t('people.mediaSpeakers.segments', sp.segment_count)}</span>
          <span>·</span>
          <span className="tabular">{t('people.mediaSpeakers.share', Math.round(share * 100))}</span>
          {sp.invalid_count > 0 && (
            <span className="inline-flex items-center gap-0.5 text-danger-fg" title={t('people.mediaSpeakers.invalidNote', sp.invalid_count)}>
              <ShieldX className="size-3" />
              {sp.invalid_count}
            </span>
          )}
        </div>
      </div>
      {has ? (
        <>
          <div className="grid grid-cols-2 gap-x-3 gap-y-2">
            <Trait label={t('people.speakerUi.sex')} value={p.sex_top ? traitLabel(sexLabel, p.sex_top) : '—'} sub={pct(p.sex_prob) != null ? `${pct(p.sex_prob)}%` : null} />
            <Trait
              label={t('people.speakerUi.age')}
              value={p.age_mean != null ? t('people.speakerUi.ageApprox', Math.round(p.age_mean)) : '—'}
              sub={topOf(p.age_group) ? ageGroupLabel(topOf(p.age_group)!.key) : null}
            />
            <Trait label={t('people.speakerUi.emotion')} value={emo ? traitLabel(emotionLabel, emo.key) : '—'} sub={emo ? `${pct(emo.value)}%` : null} />
            <Trait label={t('people.speakerUi.speechFlow')} value={flow ? traitLabel(speechFlowLabel, flow.key) : '—'} sub={flow ? `${pct(flow.value)}%` : null} />
          </div>
          {vq.length > 0 && (
            <div className="flex min-w-0 flex-wrap gap-1">
              {vq.map(([k, v]) => (
                <span key={k} className="inline-flex h-5 max-w-full items-center gap-1 rounded border border-border px-1.5 text-[11px] text-fg-2">
                  <span className="truncate">{traitLabel(voiceQualityLabel, k)}</span>
                  <span className="tabular text-fg-3">{pct(v)}%</span>
                </span>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="text-[12px] text-fg-3">{sp.segment_count ? t('people.mediaSpeakers.noProfile') : t('people.mediaSpeakers.diarizedOnly')}</p>
      )}
      <div className="mt-auto flex min-w-0 items-center gap-1.5 border-t border-border pt-2 text-[11px] text-fg-3">
        <Users className="size-3.5 shrink-0" />
        {sp.cluster ? <span className="truncate font-medium text-fg-2">{speakerName(sp.cluster)}</span> : <span className="truncate">{t('people.mediaSpeakers.notClustered')}</span>}
        <span className="ml-auto shrink-0 text-primary opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">{t('people.mediaSpeakers.details')} ›</span>
      </div>
    </button>
  )
}

/** One representative segment: play / pause in place (shares the page's sentence player), karaoke while playing. */
function SampleRow({ s, units, player }: { s: SegmentSummary; units: ReturnType<typeof toUnits>; player: Player }) {
  const t = useT()
  const active = player.id === s.id && player.status !== 'idle'
  const playing = active && player.status !== 'paused'
  const time = useClock(player.clock, active)
  return (
    <li className={cn('flex min-w-0 items-start gap-3 rounded-lg px-2 py-2', active ? 'hl-row-active' : 'hover:bg-surface-2/60')}>
      <button
        type="button"
        onClick={() => player.play(s)}
        aria-label={playing ? t('people.mediaSpeakers.pause') : t('people.mediaSpeakers.play')}
        className={cn(
          'grid size-8 shrink-0 place-items-center rounded-full transition-colors [&_svg]:size-3.5',
          active ? 'bg-[var(--spk-pill)] text-[var(--spk-on)]' : 'bg-surface-2 text-fg-2 hover:bg-[var(--spk-soft)]',
        )}
      >
        {active && player.status === 'loading' ? <Loader2 className="animate-spin" /> : playing ? <Pause /> : <Play className="translate-x-px" />}
      </button>
      <div className="min-w-0 flex-1">
        <p className="text-[14px] leading-6 break-cjk">
          {units ? (
            <KaraokeText units={units} time={time} onSeek={(at) => player.play(s, at)} />
          ) : (
            <span className={cn('rounded-[3px]', active && 'hl-sentence')}>{s.text}</span>
          )}
        </p>
        <div className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] tabular text-fg-3">
          <span className="font-mono">{fmtTimestamp(s.start_sec).replace(/\.\d+$/, '')}</span>
          <span>{fmtSec(s.duration)}</span>
          {s.snr != null && <span>SNR {Math.round(s.snr)} dB</span>}
        </div>
      </div>
    </li>
  )
}

function SpeakerDialog({ mediaId, sp, player, onClose }: { mediaId: string; sp: MediaSpeaker | null; player: Player; onClose: () => void }) {
  const t = useT()
  const segs = useMediaSegments(mediaId)
  const words = useMediaWords(mediaId, !!sp)
  const samples = useMemo(() => {
    if (!sp || !segs.data) return []
    const byId = new Map(segs.data.items.map((s) => [s.id, s]))
    return sp.sample_segment_ids.map((id) => byId.get(id)).filter((s): s is SegmentSummary => !!s)
  }, [sp, segs.data])
  const name = sp ? localSpeakerName(sp.color_index) : ''
  const color = sp ? speakerColor(sp.color_index) : undefined
  const p = sp?.profile
  return (
    <Dialog
      open={!!sp}
      onOpenChange={(o) => !o && onClose()}
      size="lg"
      title={
        sp && (
          <span className="flex min-w-0 items-center gap-2">
            <span aria-hidden className="size-3 shrink-0 rounded-full" style={{ background: color }} />
            <span className="truncate">{name}</span>
            <span className="font-mono text-xs font-normal text-fg-3">{sp.local_speaker}</span>
          </span>
        )
      }
      description={
        sp &&
        [
          `${t('people.mediaSpeakers.speakingTime')} ${fmtClock(sp.total_sec)}`,
          t('people.mediaSpeakers.segments', sp.segment_count),
          sp.first_sec != null ? t('people.mediaSpeakers.firstAt', fmtTimestamp(sp.first_sec).replace(/\.\d+$/, '')) : null,
          sp.profiled_segments ? t('people.mediaSpeakers.profiled', sp.profiled_segments, sp.segment_count) : null,
        ]
          .filter(Boolean)
          .join(' · ')
      }
    >
      {sp && p && (
        <div className="space-y-5 pb-2" style={speakerVars(sp.color_index)} data-speaker-dialog>
          {/* cross-video candidate speaker */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-surface-2/70 px-3 py-2.5">
            <Users className="size-4 shrink-0 text-fg-3" />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] text-fg-3">{t('people.mediaSpeakers.cluster')}</div>
              {sp.cluster ? (
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <span className="truncate text-[13px] font-medium">{speakerName(sp.cluster)}</span>
                  {sp.cluster.display_name && <span className="font-mono text-[11px] text-fg-3">{sp.cluster.label}</span>}
                  {sp.cluster.share < 0.995 && <span className="text-[11px] text-fg-3">{t('people.mediaSpeakers.clusterShare', Math.round(sp.cluster.share * 100))}</span>}
                </div>
              ) : (
                <div className="text-[13px] text-fg-3">{t('people.mediaSpeakers.notClustered')}</div>
              )}
            </div>
            {sp.cluster && (
              <Link to={`/speakers/${sp.cluster.id}`} className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md px-2 text-[13px] font-medium text-primary hover:bg-primary-soft">
                {t('people.mediaSpeakers.openCluster')} <ArrowUpRight className="size-3.5" />
              </Link>
            )}
          </div>

          {sp.profiled_segments > 0 ? (
            <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
              <div className="min-w-0">
                <div className="mb-1.5 text-[11px] font-medium text-fg-3">{t('people.speakerUi.sex')}</div>
                <DistBars d={p.sex} labels={sexLabel} max={2} color={color} />
              </div>
              <div className="min-w-0">
                <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[11px] font-medium text-fg-3">
                  <span>{t('people.mediaSpeakers.ageGroup')}</span>
                  {p.age_mean != null && <span className="font-normal">{t('people.mediaSpeakers.meanAge')} {t('people.speakerUi.ageApprox', Math.round(p.age_mean))}</span>}
                </div>
                <DistBars d={Object.fromEntries(Object.entries(p.age_group).map(([k, v]) => [ageGroupLabel(k), v]))} labels={{}} max={4} color={color} />
              </div>
              <div className="min-w-0">
                <div className="mb-1.5 text-[11px] font-medium text-fg-3">{t('people.speakerUi.emotion')}</div>
                <DistBars d={p.emotion} labels={emotionLabel} max={5} color={color} />
              </div>
              <div className="min-w-0">
                <div className="mb-1.5 text-[11px] font-medium text-fg-3">{t('people.speakerUi.voiceQuality')}</div>
                <DistBars d={p.voice_quality} labels={voiceQualityLabel} max={5} color={color} />
              </div>
              <div className="min-w-0">
                <div className="mb-1.5 text-[11px] font-medium text-fg-3">{t('people.speakerUi.speechFlow')}</div>
                <DistBars d={p.speech_flow} labels={speechFlowLabel} max={3} color={color} />
              </div>
              <div className="min-w-0">
                <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-fg-3">
                  {t('people.mediaSpeakers.accentBroad')}
                  <Badge tone="warning" className="h-[18px] px-1.5 text-[10px]">
                    {t('people.speakerUi.referenceOnly')}
                  </Badge>
                </div>
                <DistBars d={p.accent_broad} labels={accentBroadLabel} max={3} color="var(--series-muted)" />
                <p className="mt-1.5 flex gap-1 text-[11px] leading-4 text-fg-3">
                  <Info className="mt-px size-3 shrink-0" /> {t('people.speakerUi.accentNote')}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-[13px] text-fg-3">{sp.segment_count ? t('people.mediaSpeakers.noProfile') : t('people.mediaSpeakers.diarizedOnly')}</p>
          )}
          {sp.invalid_count > 0 && (
            <p className="flex items-center gap-1.5 text-[12px] text-fg-3">
              <ShieldX className="size-3.5 text-danger" /> {t('people.mediaSpeakers.invalidNote', sp.invalid_count)}
            </p>
          )}

          <div>
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3">
              <h3 className="text-sm font-semibold">{t('people.mediaSpeakers.samples')}</h3>
              <span className="text-[11px] text-fg-3">{t('people.mediaSpeakers.samplesDesc')}</span>
            </div>
            {!segs.data ? (
              <Skeleton className="h-24" />
            ) : samples.length ? (
              <ul className="-mx-2 space-y-0.5">
                {samples.map((s) => (
                  <SampleRow key={s.id} s={s} units={toUnits(words.data?.words[s.id], s.start_sec, s.end_sec)} player={player} />
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-fg-3">{t('people.mediaSpeakers.noSamples')}</p>
            )}
          </div>
        </div>
      )}
    </Dialog>
  )
}

export function SpeakersCard({ mediaId, player }: { mediaId: string; player: Player }) {
  const t = useT()
  const q = useMediaSpeakers(mediaId)
  const [open, setOpen] = useState<string | null>(null)
  const list = q.data ?? []
  const total = list.reduce((a, s) => a + s.total_sec, 0)
  const current = list.find((s) => s.local_speaker === open) ?? null
  if (q.isSuccess && !list.length) return null
  return (
    <Card data-shot="media-speakers">
      <CardHeader title={<span className="inline-flex items-center gap-2">{t('people.mediaSpeakers.title')}{list.length > 0 && <span className="text-[13px] font-normal text-fg-3 tabular">{t('people.mediaSpeakers.count', list.length)}</span>}</span>} description={t('people.mediaSpeakers.description')} />
      <CardBody>
        {q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data ? (
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
            <Skeleton className="h-48 rounded-xl" />
            <Skeleton className="h-48 rounded-xl" />
          </div>
        ) : !list.length ? (
          <EmptyState compact icon={<Users />} title={t('people.mediaSpeakers.emptyTitle')} description={t('people.mediaSpeakers.emptyDesc')} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
            {list.map((sp) => (
              <SpeakerCard key={sp.local_speaker} sp={sp} share={total ? sp.total_sec / total : 0} onOpen={() => setOpen(sp.local_speaker)} />
            ))}
          </div>
        )}
      </CardBody>
      <SpeakerDialog mediaId={mediaId} sp={current} player={player} onClose={() => setOpen(null)} />
    </Card>
  )
}
