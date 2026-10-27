import { msgs, t, useT } from '@/i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  AudioWaveform,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleSlash,
  Keyboard,
  ListFilter,
  Pause,
  Play,
  Repeat,
  RotateCcw,
  Save,
  TriangleAlert,
  UserRound,
} from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useChannels, useSegment, useSegmentPatch, useSegments, useSimilar, useTimeline } from '@/api/queries'
import type { ReviewStatus, SegmentDetail, SpeakerState } from '@/api/types'
import { fmtDb, fmtInt, fmtMos, fmtPct, fmtRelative, fmtTimestamp } from '@/lib/format'
import { flagLabel, reviewStatus, speakerState, textSource } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useHotkeys } from '@/hooks/useHotkeys'
import { useTheme } from '@/hooks/useTheme'
import { useIsDesktop } from '@/hooks/useMedia'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Kbd, Skeleton, Tag } from '@/components/ui/primitives'
import { Input, Segmented, Select, Switch, Textarea } from '@/components/ui/form'
import { Dialog, Sheet, Tooltip } from '@/components/ui/overlay'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { ReviewBadge } from '@/components/domain/status'
import { ProfileTraits, speakerName } from '@/components/domain/speakers'
import { LaneLabels, Lanes, lanesHeight, type LaneKey } from '@/components/domain/lanes'
import { useSize } from '@/components/charts/charts'
import { PAD, useSegmentAudio } from './useSegmentAudio'
import { Waveform } from './Waveform'

const LIMIT = 100
const NUDGE = 0.05

const SHORTCUTS = (): [string[], string][] => {
  const k = msgs().process.review.shortcuts
  return [
    [['Space'], k.play],
    [['R'], k.replay],
    [['J', '→'], k.next],
    [['K', '←'], k.prev],
    [['1'], k.pass],
    [['2'], k.fix],
    [['3'], k.exclude],
    [['E'], k.edit],
    [['['], k.startEarlier],
    [[']'], k.startLater],
    [['{'], k.endEarlier],
    [['}'], k.endLater],
    [['Ctrl/⌘', 'Enter'], k.saveText],
    [['?'], k.help],
  ]
}

/* ───────────── queue filters (URL) ───────────── */
function useQueueParams() {
  const [sp, setSp] = useSearchParams()
  const p = {
    media: sp.get('media') ?? '',
    channel: sp.get('channel') ?? '',
    status: (sp.get('status') ?? 'unchecked') as ReviewStatus | 'all',
    source: sp.get('source') ?? '',
    sort: sp.get('sort') ?? '',
    segment: sp.get('segment') ?? '',
    offset: Number(sp.get('offset') ?? 0),
  }
  const set = (patch: Record<string, string | number | null>) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === '') n.delete(k)
      else n.set(k, String(v))
    }
    setSp(n, { replace: true })
  }
  return { p, set }
}

function QueueFilters({ p, set, onDone }: { p: ReturnType<typeof useQueueParams>['p']; set: ReturnType<typeof useQueueParams>['set']; onDone?: () => void }) {
  const ch = useChannels()
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
      <Segmented
        aria-label={t('process.review.statusFilter')}
        value={p.status}
        onChange={(v) => (set({ status: v === 'unchecked' ? null : v, segment: null, offset: null }), onDone?.())}
        options={[
          { value: 'unchecked', label: t('process.review.statusUnchecked') },
          { value: 'needs_fix', label: t('process.review.statusFix') },
          { value: 'passed', label: t('process.review.statusPassed') },
          { value: 'all', label: t('process.review.statusAll') },
        ]}
      />
      <Select aria-label={t('process.review.channel')} value={p.channel} onChange={(e) => set({ channel: e.target.value, media: null, segment: null, offset: null })} className="lg:w-44" size_="sm">
        <option value="">{t('process.review.allChannels')}</option>
        {ch.data?.items.map((c) => (
          <option key={c.id} value={c.id}>
            {c.title}
          </option>
        ))}
      </Select>
      <Select aria-label={t('process.review.textSource')} value={p.source} onChange={(e) => set({ source: e.target.value, segment: null, offset: null })} className="lg:w-36" size_="sm">
        <option value="">{t('process.review.allSources')}</option>
        <option value="manual_caption">{textSource.manual_caption}</option>
        <option value="auto_caption">{textSource.auto_caption}</option>
        <option value="human_revision">{textSource.human_revision}</option>
      </Select>
      <Select aria-label={t('process.review.sort')} value={p.sort} onChange={(e) => set({ sort: e.target.value, segment: null, offset: null })} className="lg:w-40" size_="sm">
        <option value="">{t('process.review.sortDefault')}</option>
        <option value="snr">{t('process.review.sortSnrAsc')}</option>
        <option value="-snr">{t('process.review.sortSnrDesc')}</option>
        <option value="-duration">{t('process.review.sortLongFirst')}</option>
      </Select>
      {p.media && (
        <span className="inline-flex h-9 items-center gap-1 rounded-full border border-primary/25 bg-primary-soft pl-3 pr-1 text-xs font-medium text-primary-soft-fg sm:h-8">
          {t('process.review.thisVideoOnly')}
          <button aria-label={t('process.review.removeVideoFilter')} className="grid size-6 place-items-center rounded-full hover:bg-primary/15" onClick={() => set({ media: null, segment: null, offset: null })}>
            ×
          </button>
        </span>
      )}
    </div>
  )
}

/* ───────────── detail side panels ───────────── */
function MetricChips({ s }: { s: SegmentDetail }) {
  const items: [string, string, boolean?][] = [
    ['SNR', fmtDb(s.snr), (s.snr ?? 99) < 10],
    ['C50', fmtDb(s.c50)],
    ['OVRL', fmtMos(s.dnsmos_ovrl), (s.dnsmos_ovrl ?? 5) < 2.5],
    ['P.808', fmtMos(s.dnsmos_p808)],
    [t('process.media.speechRatio'), fmtPct(s.speech_ratio), (s.speech_ratio ?? 1) < 0.5],
    [t('process.review.overlapRepeated'), fmtPct(s.repeated_overlap), s.repeated_overlap > 0.2],
  ]
  return (
    <div className="grid grid-cols-3 gap-1.5">
      {items.map(([k, v, bad]) => (
        <div key={k} className={cn('min-w-0 rounded-lg px-2.5 py-1.5', bad ? 'bg-warning-soft' : 'bg-surface-2/70')}>
          <div className="truncate text-[10px] text-fg-3">{k}</div>
          <div className={cn('truncate text-[13px] font-semibold tabular', bad && 'text-warning-fg')}>{v}</div>
        </div>
      ))}
    </div>
  )
}

function SpeakerPanel({ s }: { s: SegmentDetail }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const patch = useSegmentPatch()
  const assign = useMutation({
    mutationFn: () => api.setSegmentSpeaker(s.id, name.trim()),
    onSuccess: (d) => {
      qc.setQueryData(qk.segment(s.id), d)
      setName('')
      toast.success(t('process.review.speakerAssigned'))
    },
    onError: onErr,
  })
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Avatar name={s.speaker ? speakerName(s.speaker) : '?'} size={36} />
        <div className="min-w-0 flex-1">
          {s.speaker ? (
            <Link to={`/speakers/${s.speaker.id}`} className="block truncate text-[13px] font-medium hover:text-primary">
              {speakerName(s.speaker)}
            </Link>
          ) : (
            <div className="text-[13px] font-medium text-fg-3">{t('process.review.unclustered')}</div>
          )}
          <div className="truncate text-[11px] text-fg-3">
            {s.speaker?.label ?? '—'} · {t('process.review.inThisVideo')} <span className="font-mono">{s.local_speaker ?? '—'}</span>
          </div>
        </div>
      </div>
      <Select
        aria-label={t('process.review.speakerState')}
        size_="sm"
        value={s.speaker_state}
        onChange={(e) => patch.mutate({ id: s.id, patch: { speaker_state: e.target.value as SpeakerState } })}
      >
        {Object.entries(speakerState).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </Select>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) assign.mutate()
        }}
      >
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('process.review.assignPlaceholder')} className="h-10 text-[13px] sm:h-8" />
        <Button type="submit" size="sm" disabled={!name.trim()} loading={assign.isPending}>
          {t('process.review.assign')}
        </Button>
      </form>
    </div>
  )
}

function SimilarPanel({ id }: { id: string }) {
  const sim = useSimilar(id)
  const [, setSp] = useSearchParams()
  if (!sim.data) return <Skeleton className="h-24" />
  if (!sim.data.length) return <p className="text-[13px] text-fg-3">{t('process.review.noSimilar')}</p>
  return (
    <ul className="space-y-1">
      {sim.data.map(({ segment: x, similarity }) => (
        <li key={x.id}>
          <button
            onClick={() => setSp((p) => (p.set('segment', x.id), p.delete('status'), p.set('media', x.media_id), p), { replace: true })}
            className="flex w-full min-w-0 items-center gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-surface-2"
          >
            <span className="w-10 shrink-0 text-right text-xs font-semibold tabular text-fg">{Math.round(similarity * 100)}%</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px]">{x.text}</span>
              <span className="block truncate text-[11px] text-fg-3">
                {x.speaker ? speakerName(x.speaker) : '—'} · {x.media_title}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

/* ───────────── main ───────────── */
export default function Review() {
  const t = useT()
  useTitle(t('process.review.title'))
  const { p, set } = useQueueParams()
  const isDesktop = useIsDesktop()
  const list = useSegments({
    media_id: p.media || undefined,
    channel_id: p.channel || undefined,
    review_status: p.status === 'all' ? undefined : [p.status],
    text_source: p.source ? [p.source] : undefined,
    sort: p.sort || undefined,
    limit: LIMIT,
    offset: p.offset,
  })
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const idx = Math.max(0, items.findIndex((x) => x.id === p.segment))
  const currentId = p.segment && items.some((x) => x.id === p.segment) ? p.segment : items[0]?.id
  const seg = useSegment(currentId)
  const s = seg.data
  const tl = useTimeline(s?.media_id ?? '', !!s)
  const patch = useSegmentPatch()
  const audio = useSegmentAudio(s?.id, s?.revision)
  const { resolved } = useTheme()
  const [waveRef, waveW] = useSize<HTMLDivElement>()
  const textRef = useRef<HTMLTextAreaElement>(null)
  const [draft, setDraft] = useState('')
  const [note, setNote] = useState('')
  const [region, setRegion] = useState<[number, number]>([PAD, PAD + 1])
  const [autoNext, setAutoNext] = useState(true)
  const [loop, setLoop] = useState(false)
  const [rate, setRate] = useState(1)
  const [help, setHelp] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)

  // reset local edit state per segment
  useEffect(() => {
    if (!s) return
    setDraft(s.corrected_text ?? s.original_text)
    setNote(s.review_note)
    setRegion([PAD, PAD + (s.end_sec - s.start_sec)])
  }, [s?.id, s?.revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const clipStart = s ? s.start_sec - PAD : 0
  const absRegion: [number, number] = [clipStart + region[0], clipStart + region[1]]
  const boundaryDirty = !!s && (Math.abs(absRegion[0] - s.start_sec) > 0.004 || Math.abs(absRegion[1] - s.end_sec) > 0.004)
  const textDirty = !!s && draft !== (s.corrected_text ?? s.original_text)

  const go = (d: number) => {
    const n = idx + d
    if (n >= 0 && n < items.length) set({ segment: items[n].id })
    else if (n >= items.length && list.data && p.offset + LIMIT < list.data.total) set({ offset: p.offset + LIMIT, segment: null })
    else if (n < 0 && p.offset > 0) set({ offset: Math.max(0, p.offset - LIMIT), segment: null })
  }
  const playRegion = () => audio.play(region[0], loop ? null : region[1])
  const toggle = () => (audio.playing ? audio.pause() : playRegion())

  // loop playback
  useEffect(() => {
    if (loop && audio.playing && audio.time >= region[1]) audio.play(region[0], null)
  }, [audio.time]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveExtras = () => ({
    ...(textDirty ? { corrected_text: draft === s!.original_text ? null : draft } : {}),
    ...(note !== s!.review_note ? { review_note: note } : {}),
    ...(boundaryDirty ? { start_sec: +absRegion[0].toFixed(3), end_sec: +absRegion[1].toFixed(3) } : {}),
  })
  const setStatus = (st: ReviewStatus) => {
    if (!s) return
    patch.mutate(
      { id: s.id, patch: { review_status: st, ...saveExtras() } },
      {
        onSuccess: () => {
          toast.success(t('process.review.markedAs', reviewStatus[st].label), { duration: 1400 })
          if (autoNext) go(1)
        },
      },
    )
  }
  const save = () => {
    if (!s) return
    const ex = saveExtras()
    if (!Object.keys(ex).length) return
    patch.mutate({ id: s.id, patch: ex }, { onSuccess: () => toast.success(boundaryDirty ? t('process.review.savedReembed') : t('common.saved')) })
  }
  const nudge = (which: 0 | 1, d: number) =>
    setRegion((r) => {
      const n: [number, number] = [...r]
      n[which] = Math.max(0, Math.min(audio.duration || r[1] + PAD, n[which] + d))
      if (n[1] - n[0] < 0.2) return r
      return n
    })

  useHotkeys(
    {
      space: toggle,
      r: () => audio.play(region[0], region[1]),
      j: () => go(1),
      arrowright: () => go(1),
      k: () => go(-1),
      arrowleft: () => go(-1),
      '1': () => setStatus('passed'),
      '2': () => setStatus('needs_fix'),
      '3': () => setStatus('excluded'),
      e: () => textRef.current?.focus(),
      '[': () => nudge(0, -NUDGE),
      ']': () => nudge(0, NUDGE),
      '{': () => nudge(1, -NUDGE),
      '}': () => nudge(1, NUDGE),
      'mod+enter': save,
      '?': () => setHelp(true),
      escape: () => (document.activeElement as HTMLElement | null)?.blur(),
    },
    { enabled: !!s, allowInInputs: ['escape'] },
  )

  const position = list.data ? p.offset + idx + 1 : 0
  const lanes: LaneKey[] = ['vad', 'snr', 'speaker', 'repeated', 'segments']

  const header = (
    <div className="mb-4 flex flex-col gap-3 lg:mb-5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight sm:text-2xl">{t('process.review.title')}</h1>
          <p className="text-[13px] text-fg-3">
            {list.data ? (
              t('process.review.position', position, list.data.total)
            ) : (
              t('process.review.loadingQueue')
            )}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {!isDesktop && (
            <Button size="sm" onClick={() => setFiltersOpen(true)}>
              <ListFilter /> {t('process.review.queue')}
            </Button>
          )}
          <Tooltip content={t('process.review.shortcutsTip')}>
            <Button size="icon-sm" variant="ghost" aria-label={t('process.review.shortcutsLabel')} onClick={() => setHelp(true)} className="hidden sm:inline-flex">
              <Keyboard />
            </Button>
          </Tooltip>
        </div>
      </div>
      {isDesktop && <QueueFilters p={p} set={set} />}
    </div>
  )

  if (list.isError)
    return (
      <Page wide>
        {header}
        <Card>
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        </Card>
      </Page>
    )
  if (list.data && !items.length)
    return (
      <Page wide>
        {header}
        <Card>
          <EmptyState
            icon={<Check />}
            title={t('process.review.emptyTitle')}
            description={t('process.review.emptyDesc')}
            action={
              <Button onClick={() => set({ status: 'all', channel: null, media: null, source: null, segment: null, offset: null })}>
                <RotateCcw /> {t('process.review.showAll')}
              </Button>
            }
          />
        </Card>
        <ShortcutDialog open={help} onOpenChange={setHelp} />
      </Page>
    )

  const actionButtons = (big?: boolean) => (
    <div className="grid grid-cols-3 gap-2">
      {(
        [
          ['passed', t('process.review.pass'), <Check key="i" />, 'success', '1'],
          ['needs_fix', t('process.review.fix'), <TriangleAlert key="i" />, 'warning', '2'],
          ['excluded', t('process.review.exclude'), <CircleSlash key="i" />, 'danger', '3'],
        ] as const
      ).map(([st, label, icon, variant, key]) => (
        <Button
          key={st}
          variant={s?.review_status === st ? variant : 'secondary'}
          size={big ? 'lg' : 'md'}
          className={cn('min-w-0', big && 'h-14 flex-col gap-0.5 text-[13px] sm:h-12 sm:flex-row sm:gap-2 sm:text-[15px]')}
          onClick={() => setStatus(st)}
          disabled={!s || patch.isPending}
        >
          {icon}
          <span className="min-w-0 truncate">{label}</span>
          <Kbd className={cn('ml-1 hidden lg:inline-flex', s?.review_status === st && 'border-white/40 bg-white/15 text-white shadow-none')}>{key}</Kbd>
        </Button>
      ))}
    </div>
  )

  return (
    <Page wide className="pb-0 md:pb-7">
      {header}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        {/* main column */}
        <div className="min-w-0 space-y-4">
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 pt-4 sm:px-5">
              <div className="min-w-0 flex-1">
                {s ? (
                  <>
                    <Link to={`/media/${s.media_id}`} className="line-clamp-1 text-[13px] font-medium hover:text-primary" title={s.media_title}>
                      {s.media_title}
                    </Link>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-3">
                      <span>{s.channel_title}</span>
                      <span className="font-mono">
                        {fmtTimestamp(s.start_sec)} – {fmtTimestamp(s.end_sec)}
                      </span>
                      <span className="tabular">{t('process.review.seconds', (absRegion[1] - absRegion[0]).toFixed(2))}</span>
                      {s.revision > 1 && <Tag>{t('process.review.revision', s.revision)}</Tag>}
                    </div>
                  </>
                ) : (
                  <Skeleton className="h-9 w-2/3" />
                )}
              </div>
              {s && <ReviewBadge status={s.review_status} />}
            </div>

            <div className="flex gap-3 px-4 pt-7 sm:px-5">
              <div className="hidden w-14 shrink-0 sm:block">
                <div className="flex h-28 items-center text-[11px] text-fg-3">{t('process.waveform.label')}</div>
                {s && tl.data && <LaneLabels lanes={lanes} className="mt-3" />}
              </div>
              <div className="min-w-0 flex-1" ref={waveRef}>
                {audio.error ? (
                  <div className="grid h-28 place-items-center rounded-lg bg-danger-soft text-[13px] text-danger-fg">{audio.error}</div>
                ) : !s || !audio.peaks ? (
                  <Skeleton className="h-28" />
                ) : (
                  waveW > 0 && (
                    <Waveform
                      key={resolved}
                      peaks={audio.peaks}
                      duration={audio.duration}
                      width={waveW}
                      region={region}
                      onRegionChange={setRegion}
                      playhead={audio.time}
                      onSeek={(t) => audio.seek(t)}
                      clipStart={clipStart}
                    />
                  )
                )}
                {s && tl.data && waveW > 0 && (
                  <div className="mt-3 overflow-hidden" style={{ height: lanesHeight(lanes) }}>
                    <Lanes
                      t={tl.data}
                      domain={[clipStart, clipStart + (audio.duration || s.end_sec - s.start_sec + 2 * PAD)]}
                      width={waveW}
                      lanes={lanes}
                      activeSegment={s.id}
                      selection={absRegion}
                      playhead={clipStart + audio.time}
                      onSegmentClick={(id) => set({ segment: id, status: items.some((x) => x.id === id) ? (p.status === 'unchecked' ? null : p.status) : 'all' })}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* transport */}
            <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 sm:px-5">
              <Button variant="primary" size="icon" className="size-12 rounded-full sm:size-10" onClick={toggle} disabled={!audio.ready} aria-label={audio.playing ? t('process.review.pause') : t('process.review.play')}>
                {audio.playing ? <Pause className="!size-5" /> : <Play className="!size-5 translate-x-px" />}
              </Button>
              <Tooltip content={t('process.review.replayTip')}>
                <Button size="icon" variant="ghost" onClick={() => audio.play(region[0], region[1])} disabled={!audio.ready} aria-label={t('process.review.replay')}>
                  <RotateCcw />
                </Button>
              </Tooltip>
              <Tooltip content={t('process.review.loop')}>
                <Button size="icon" variant={loop ? 'soft' : 'ghost'} onClick={() => setLoop((l) => !l)} aria-pressed={loop} aria-label={t('process.review.loop')}>
                  <Repeat />
                </Button>
              </Tooltip>
              <Select
                aria-label={t('process.review.speed')}
                size_="sm"
                value={String(rate)}
                onChange={(e) => {
                  setRate(Number(e.target.value))
                  audio.setRate(Number(e.target.value))
                }}
                className="w-[5.5rem]"
              >
                {[0.75, 1, 1.25, 1.5].map((r) => (
                  <option key={r} value={r}>
                    {r}×
                  </option>
                ))}
              </Select>
              <span className="flex-1" />
              <div className="flex items-center gap-1 rounded-lg bg-surface-2/70 p-1">
                <span className="px-1.5 text-[11px] text-fg-3">{t('process.review.start')}</span>
                <Button size="icon-sm" variant="ghost" aria-label={t('process.review.startEarlier')} onClick={() => nudge(0, -NUDGE)}>
                  <ChevronLeft />
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label={t('process.review.startLater')} onClick={() => nudge(0, NUDGE)}>
                  <ChevronRight />
                </Button>
                <span className="mx-1 h-5 w-px bg-border-strong" />
                <span className="px-1.5 text-[11px] text-fg-3">{t('process.review.end')}</span>
                <Button size="icon-sm" variant="ghost" aria-label={t('process.review.endEarlier')} onClick={() => nudge(1, -NUDGE)}>
                  <ChevronLeft />
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label={t('process.review.endLater')} onClick={() => nudge(1, NUDGE)}>
                  <ChevronRight />
                </Button>
              </div>
              {boundaryDirty && (
                <Button size="sm" variant="ghost" onClick={() => s && setRegion([PAD, PAD + s.end_sec - s.start_sec])}>
                  {t('process.review.resetBoundary')}
                </Button>
              )}
            </div>
          </Card>

          {/* text */}
          <Card>
            <CardBody className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label htmlFor="seg-text" className="flex items-center gap-2 text-[13px] font-medium">
                  {t('process.review.text')}
                  {s && <Badge tone={s.text_source === 'auto_caption' ? 'warning' : s.text_source === 'human_revision' ? 'success' : 'neutral'}>{textSource[s.text_source] ?? s.text_source}</Badge>}
                </label>
                <span className="hidden text-[11px] text-fg-3 sm:inline">
                  <Kbd>E</Kbd> {t('process.review.editHint')} · <Kbd>Ctrl</Kbd>+<Kbd>Enter</Kbd> {t('process.review.saveHint')}
                </span>
              </div>
              <Textarea
                id="seg-text"
                ref={textRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                className="text-[17px] leading-8 sm:text-base sm:leading-7"
                disabled={!s}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    save()
                  }
                }}
              />
              {s && (textDirty || s.corrected_text) && (
                <div className="rounded-lg bg-surface-2/70 px-3 py-2 text-[13px]">
                  <span className="text-[11px] text-fg-3">{t('process.review.originalCaption')}</span>
                  <p className="mt-0.5 text-fg-2 break-cjk">{s.original_text}</p>
                </div>
              )}
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t('process.review.notePlaceholder')} className="text-[13px]" disabled={!s} />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Switch checked={autoNext} onCheckedChange={setAutoNext} label={t('process.review.autoNext')} className="min-h-0" />
                <Button size="sm" onClick={save} disabled={!s || (!textDirty && !boundaryDirty && note === s.review_note)} loading={patch.isPending}>
                  <Save /> {t('process.review.saveChanges')}
                </Button>
              </div>
            </CardBody>
          </Card>

          {/* desktop/tablet action bar */}
          <div className="hidden items-center gap-2 md:flex">
            <Button onClick={() => go(-1)} disabled={position <= 1}>
              <ChevronLeft /> {t('process.review.prev')}
            </Button>
            <div className="flex-1">{actionButtons()}</div>
            <Button onClick={() => go(1)}>
              {t('process.review.next')} <ChevronRight />
            </Button>
          </div>
        </div>

        {/* side column */}
        <div className="min-w-0 space-y-4">
          <Card>
            <CardHeader title={t('process.review.quality')} description={s?.flags.length ? undefined : t('process.review.noFlags')} />
            <CardBody className="space-y-3">
              {s ? <MetricChips s={s} /> : <Skeleton className="h-24" />}
              {s && s.flags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {s.flags.map((f) => (
                    <Badge key={f} tone="warning">
                      {flagLabel[f] ?? f}
                    </Badge>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
          <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-1">
            <Card>
              <CardHeader title={t('process.review.speaker')} icon={<UserRound />} />
              <CardBody>{s ? <SpeakerPanel s={s} /> : <Skeleton className="h-24" />}</CardBody>
            </Card>
            <Card>
              <CardHeader title={t('process.review.profileTitle')} description={t('process.review.profileDesc')} />
              <CardBody>
                {!s ? <Skeleton className="h-40" /> : s.profile ? <ProfileTraits p={s.profile} compact /> : <p className="text-[13px] text-fg-3">{t('process.review.noProfile')}</p>}
              </CardBody>
            </Card>
          </div>
          <Card>
            <CardHeader title={t('process.review.similar')} description={t('process.review.similarDesc')} icon={<AudioWaveform />} />
            <CardBody className="pt-2">{s ? <SimilarPanel id={s.id} /> : <Skeleton className="h-24" />}</CardBody>
          </Card>
          {s && s.history.length > 0 && (
            <Card>
              <CardHeader title={t('process.review.history')} />
              <CardBody>
                <ol className="space-y-2.5">
                  {s.history.slice(0, 5).map((h) => (
                    <li key={h.id} className="text-[13px]">
                      <span className="font-medium">{h.user?.display_name ?? t('process.review.system')}</span>
                      <span className="text-fg-3"> · {fmtRelative(h.at)}</span>
                      <div className="truncate text-xs text-fg-3">{Object.keys(h.after ?? {}).join(t('common.listSep')) || h.action}</div>
                    </li>
                  ))}
                </ol>
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      {/* phone sticky action bar (above bottom tabs) */}
      <div className="sticky bottom-[calc(64px+env(safe-area-inset-bottom))] z-20 -mx-4 mt-4 border-t border-border bg-surface/95 px-3 pb-3 pt-2 backdrop-blur-xl sm:-mx-6 md:hidden">
        <div className="mb-2 flex items-center justify-between">
          <Button size="sm" variant="ghost" onClick={() => go(-1)} disabled={position <= 1}>
            <ChevronLeft /> {t('process.review.prev')}
          </Button>
          <span className="text-xs tabular text-fg-3">
            {fmtInt(position)} / {fmtInt(list.data?.total ?? 0)}
          </span>
          <Button size="sm" variant="ghost" onClick={() => go(1)}>
            {t('process.review.next')} <ChevronRight />
          </Button>
        </div>
        {actionButtons(true)}
      </div>

      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen} title={t('process.review.queueTitle')} description={t('process.review.queueDesc')}>
        <div className="p-4">
          <QueueFilters p={p} set={set} />
        </div>
      </Sheet>
      <ShortcutDialog open={help} onOpenChange={setHelp} />
    </Page>
  )
}

function ShortcutDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={t('process.review.shortcutsLabel')} description={t('process.review.shortcutsDesc')} size="sm">
      <ul className="divide-y divide-border">
        {SHORTCUTS().map(([keys, label]) => (
          <li key={label} className="flex items-center justify-between py-2 text-[13px]">
            <span className="text-fg-2">{label}</span>
            <span className="flex gap-1">
              {keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Dialog>
  )
}
