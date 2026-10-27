import { msgs, t, useT } from '@/i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowLeft,
  AudioWaveform,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleSlash,
  Keyboard,
  Loader2,
  Pause,
  Play,
  Repeat,
  RotateCcw,
  Save,
  TriangleAlert,
  UserRound,
  Wand2,
  ShieldX,
} from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useNeighbors, useSegment, useSegmentPatch, useSegments, useSimilar, useTimeline } from '@/api/queries'
import type { ReviewStatus, SegmentDetail, SpeakerState } from '@/api/types'
import { fmtDb, fmtMos, fmtPct, fmtRelative, fmtTimestamp } from '@/lib/format'
import { flagLabel, lang, reviewStatus, speakerState, textSource } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useHotkeys } from '@/hooks/useHotkeys'
import { useTheme } from '@/hooks/useTheme'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Kbd, Skeleton, Tag } from '@/components/ui/primitives'
import { Input, Select, Switch, Textarea } from '@/components/ui/form'
import { Dialog, Tooltip } from '@/components/ui/overlay'
import { ErrorState } from '@/components/ui/states'
import { ReviewBadge } from '@/components/domain/status'
import { ValidityBadge, ValidityControls, invalidText, lidLangName } from '@/components/domain/validity'
import { ProfileTraits, speakerName } from '@/components/domain/speakers'
import { LaneLabels, Lanes, lanesHeight, type LaneKey } from '@/components/domain/lanes'
import { useSize } from '@/components/charts/charts'
import { PAD, useSegmentAudio } from './useSegmentAudio'
import { Waveform } from './Waveform'
import { KaraokeText, toUnits } from '@/components/domain/karaoke'
import { LIST_LIMIT, toSegmentQuery, useReviewParams } from './params'

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
    [['Backspace'], k.back],
    [['?'], k.help],
  ]
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
  const navigate = useNavigate()
  if (!sim.data) return <Skeleton className="h-24" />
  if (!sim.data.length) return <p className="text-[13px] text-fg-3">{t('process.review.noSimilar')}</p>
  return (
    <ul className="space-y-1">
      {sim.data.map(({ segment: x, similarity }) => (
        <li key={x.id}>
          <button
            onClick={() => navigate(`/review/${x.id}?media=${x.media_id}&status=all`)}
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

/* ───────────── karaoke line ───────────── */
function SpokenLine({ s, time, onSeek }: { s: SegmentDetail; time: number | null; onSeek: (abs: number) => void }) {
  const qc = useQueryClient()
  const units = useMemo(() => toUnits(s.words, s.start_sec, s.end_sec), [s.words, s.start_sec, s.end_sec])
  const align = useMutation({
    mutationFn: () => api.alignMedia(s.media_id),
    onSuccess: () => {
      toast.success(t('process.review.alignQueued'))
      qc.invalidateQueries({ queryKey: qk.segment(s.id) })
    },
    onError: onErr,
  })
  const al = s.alignment
  const pending = al?.status === 'pending'
  // a re-alignment after a text/boundary edit finishes within seconds: refetch until it does
  useEffect(() => {
    if (!pending) return
    const id = setInterval(() => qc.invalidateQueries({ queryKey: qk.segment(s.id) }), 4000)
    return () => clearInterval(id)
  }, [pending, s.id, qc])
  const reason = (r: string | null | undefined) => {
    if (!r) return null
    const [code, arg] = r.split(':')
    if ((code === 'no_romanizer' || code === 'unsupported_language') && arg) return t('process.review.alignUnsupported', lang(arg))
    if (code === 'too_long') return t('process.review.alignTooLong')
    if (code === 'no_text') return t('process.review.alignNoText')
    return r
  }
  return (
    <div className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-start sm:gap-4">
      <p className="min-w-0 flex-1 text-[19px] leading-9 tracking-wide break-cjk sm:text-xl sm:leading-9">
        {units ? (
          <KaraokeText units={units} time={time} onSeek={onSeek} />
        ) : (
          <span className={cn('rounded-[3px] transition-colors', time != null && time < s.end_sec && 'bg-primary-soft text-primary-soft-fg shadow-[0_0_0_2px_var(--primary-soft)] [box-decoration-break:clone]')}>{s.text}</span>
        )}
      </p>
      {units ? (
        <span className="hidden shrink-0 pt-2 text-[11px] text-fg-3 lg:block">{t('process.review.clickToSeek')}</span>
      ) : pending ? (
        <Badge tone="primary" className="shrink-0 sm:mt-1.5">
          <Loader2 className="size-3 animate-spin" /> {t('process.review.aligning')}
        </Badge>
      ) : al?.status === 'skipped' ? (
        <span className="shrink-0 text-[11px] text-fg-3 sm:pt-2">{reason(al.reason) ?? t('process.review.notAligned')}</span>
      ) : (
        <span className="flex shrink-0 items-center gap-1.5 sm:pt-1.5">
          <Tooltip content={reason(al?.reason) ?? t('process.review.notAlignedTip')}>
            <Badge tone={al?.status === 'failed' ? 'warning' : 'neutral'} dot>
              {al?.status === 'failed' ? t('process.review.alignFailed') : t('process.review.notAligned')}
            </Badge>
          </Tooltip>
          <Button size="xs" variant="ghost" onClick={() => align.mutate()} loading={align.isPending}>
            <Wand2 /> {t('process.review.align')}
          </Button>
        </span>
      )}
    </div>
  )
}

/* ───────────── main ───────────── */
/** Caption ↔ audio check: spoken language (top-3), alignment confidence, manual override. */
function CaptionCheck({ s }: { s: SegmentDetail }) {
  const t = useT()
  const lid = s.validation?.lid ?? (s.lid_lang ? ([[s.lid_lang, s.lid_prob ?? 0]] as [string, number][]) : null)
  return (
    <div className="space-y-2 border-t border-border pt-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{t('validate.tip.title')}</span>
        <Badge tone={s.validity === 'invalid' ? 'danger' : s.validity === 'valid' ? 'success' : 'neutral'} dot>
          {t(`validate.validity.${s.validity ?? 'unknown'}`)}
        </Badge>
        {s.validity_source === 'manual' && <Tag>{t('validate.manual')}</Tag>}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
        <dt className="text-fg-3">{t('validate.tip.lid')}</dt>
        <dd className="min-w-0 break-cjk tabular">{lid?.length ? lid.map(([c, p]) => `${lidLangName(c)} ${Math.round(p * 100)}%`).join(' · ') : '—'}</dd>
        <dt className="text-fg-3">{t('validate.tip.align')}</dt>
        <dd className="tabular">{s.align_score != null ? s.align_score.toFixed(2) : '—'}</dd>
      </dl>
      {s.validity !== 'invalid' && <ValidityControls s={s} />}
    </div>
  )
}

export default function Review() {
  const t = useT()
  useTitle(t('process.review.title'))
  const { segmentId = '' } = useParams()
  const { p, sp } = useReviewParams()
  const navigate = useNavigate()
  const query = useMemo(() => toSegmentQuery(p), [sp.toString()]) // eslint-disable-line react-hooks/exhaustive-deps
  const nb = useNeighbors(segmentId, query)
  // fallback when the API has no neighbors endpoint: locate the segment in the list window it was opened from
  const win = useSegments({ ...query, limit: 500, offset: p.offset }, nb.isError)
  const nav = useMemo(() => {
    if (nb.data && !nb.isPlaceholderData) return nb.data
    if (nb.isError && win.data) {
      const i = win.data.items.findIndex((x) => x.id === segmentId)
      if (i < 0) return { prev_id: null, next_id: null, index: null, total: win.data.total }
      return { prev_id: win.data.items[i - 1]?.id ?? null, next_id: win.data.items[i + 1]?.id ?? null, index: p.offset + i, total: win.data.total }
    }
    return nb.data ?? null
  }, [nb.data, nb.isPlaceholderData, nb.isError, win.data, segmentId, p.offset])
  const seg = useSegment(segmentId)
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

  // reset local edit state per segment
  useEffect(() => {
    if (!s) return
    setDraft(s.corrected_text ?? s.original_text)
    setNote(s.review_note)
    const l = Math.min(PAD, s.start_sec)
    setRegion([l, l + (s.end_sec - s.start_sec)])
  }, [s?.id, s?.revision]) // eslint-disable-line react-hooks/exhaustive-deps

  // The clip starts PAD seconds before the segment, except near the media start where the server clamps at 0 s —
  // so all clip-relative times use the real clip start (X-Clip-Start), falling back to the same clamp.
  const clipStart = s ? (audio.clipStart ?? Math.max(0, s.start_sec - PAD)) : 0
  const lead = s ? s.start_sec - clipStart : PAD
  const absRegion: [number, number] = [clipStart + region[0], clipStart + region[1]]
  const boundaryDirty = !!s && (Math.abs(absRegion[0] - s.start_sec) > 0.004 || Math.abs(absRegion[1] - s.end_sec) > 0.004)
  const textDirty = !!s && draft !== (s.corrected_text ?? s.original_text)

  const qsFor = (patchQs: Record<string, string | null> = {}) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patchQs)) v === null ? n.delete(k) : n.set(k, v)
    const q = n.toString()
    return q ? `?${q}` : ''
  }
  const open = (id: string | null | undefined) => id && navigate(`/review/${id}${qsFor()}`, { replace: true })
  const backToList = () => {
    const off = nav?.index != null ? Math.floor(nav.index / LIST_LIMIT) * LIST_LIMIT : p.offset
    navigate(`/review${qsFor({ offset: off ? String(off) : null })}`, { state: { focus: segmentId } })
  }
  const go = (d: 1 | -1) => open(d > 0 ? nav?.next_id : nav?.prev_id)
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
    // the neighbour is resolved before the change: the marked segment may drop out of the filtered list
    const next = nav?.next_id ?? null
    patch.mutate(
      { id: s.id, patch: { review_status: st, ...saveExtras() } },
      {
        onSuccess: () => {
          toast.success(t('process.review.markedAs', reviewStatus[st].label), { duration: 1400 })
          if (!autoNext) return
          if (next) open(next)
          else {
            toast(t('process.review.lastInList'), { duration: 2200 })
            backToList()
          }
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
      backspace: backToList,
    },
    { enabled: !!s, allowInInputs: ['escape'] },
  )

  const position = nav?.index != null ? nav.index + 1 : null
  const lanes: LaneKey[] = ['vad', 'snr', 'speaker', 'repeated', 'segments']
  const spokenTime = s && (audio.playing || audio.time > 0) ? clipStart + audio.time : null

  const header = (
    <div className="mb-4 flex items-center gap-2 sm:gap-3 lg:mb-5">
      <Button onClick={backToList} className="shrink-0" aria-keyshortcuts="Backspace">
        <ArrowLeft /> <span>{t('process.review.backToList')}</span>
      </Button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-lg font-semibold leading-7 tracking-tight sm:text-xl">{t('process.review.title')}</h1>
        <p className="truncate text-[13px] text-fg-3">
          {position != null && nav ? t('process.review.position', position, nav.total) : nav && nav.index == null ? t('process.review.notInList') : t('process.review.loadingQueue')}
        </p>
      </div>
      <div className="hidden items-center gap-1 sm:flex">
        <Tooltip content={`${t('process.review.prev')} (K)`}>
          <Button size="icon" variant="ghost" onClick={() => go(-1)} disabled={!nav?.prev_id} aria-label={t('process.review.prev')}>
            <ChevronLeft />
          </Button>
        </Tooltip>
        <Tooltip content={`${t('process.review.next')} (J)`}>
          <Button size="icon" variant="ghost" onClick={() => go(1)} disabled={!nav?.next_id} aria-label={t('process.review.next')}>
            <ChevronRight />
          </Button>
        </Tooltip>
        <Tooltip content={t('process.review.shortcutsTip')}>
          <Button size="icon" variant="ghost" aria-label={t('process.review.shortcutsLabel')} onClick={() => setHelp(true)}>
            <Keyboard />
          </Button>
        </Tooltip>
      </div>
    </div>
  )

  if (seg.isError)
    return (
      <Page wide>
        {header}
        <Card>
          <ErrorState error={seg.error} onRetry={() => seg.refetch()} />
        </Card>
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
              {s && (
                <div className="flex flex-wrap items-center gap-2">
                  {s.validity !== 'invalid' && <ValidityBadge s={s} />}
                  <ReviewBadge status={s.review_status} />
                </div>
              )}
            </div>
            {s?.validity === 'invalid' && (
              <div role="status" data-shot="invalid-banner" className="mx-4 mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-danger/25 bg-danger-soft/60 px-3 py-2 sm:mx-5">
                <ShieldX className="size-4 shrink-0 text-danger" />
                <div className="min-w-0 flex-1 text-[13px]">
                  <div className="font-medium text-danger-fg break-cjk">
                    {invalidText(s)}
                    {s.validity_source === 'manual' && s.invalid_reason !== 'manual' && <span className="ml-1.5 font-normal opacity-80">· {t('validate.manual')}</span>}
                  </div>
                  <div className="text-xs text-fg-2 break-cjk">{t('validate.tip.excluded')}</div>
                </div>
                <ValidityControls s={s} />
              </div>
            )}

            {/* the sentence, highlighted character by character while it plays */}
            <div className="px-4 pt-4 sm:px-5">
              {s ? <SpokenLine s={s} time={spokenTime} onSeek={(abs) => audio.play(Math.max(0, abs - clipStart), region[1])} /> : <Skeleton className="h-9 w-full" />}
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
                      onSegmentClick={(id) => id !== s.id && navigate(`/review/${id}${qsFor()}`)}
                    />
                  </div>
                )}
              </div>
            </div>

            {/* transport */}
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 sm:px-5">
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
                <Button size="sm" variant="ghost" onClick={() => s && setRegion([lead, lead + s.end_sec - s.start_sec])}>
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
                  {s?.flags.includes('s2t_converted') && <Badge tone="info">{flagLabel.s2t_converted}</Badge>}
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
            <Button onClick={() => go(-1)} disabled={!nav?.prev_id}>
              <ChevronLeft /> {t('process.review.prev')}
            </Button>
            <div className="flex-1">{actionButtons()}</div>
            <Button onClick={() => go(1)} disabled={!nav?.next_id}>
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
                    <Badge key={f} tone={f === 's2t_converted' ? 'info' : 'warning'}>
                      {flagLabel[f] ?? f}
                    </Badge>
                  ))}
                </div>
              )}
              {s && <CaptionCheck s={s} />}
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
          <Button size="sm" variant="ghost" onClick={() => go(-1)} disabled={!nav?.prev_id}>
            <ChevronLeft /> {t('process.review.prev')}
          </Button>
          <span className="text-xs tabular text-fg-3">{position != null && nav ? t('process.review.positionOf', position, nav.total) : '—'}</span>
          <Button size="sm" variant="ghost" onClick={() => go(1)} disabled={!nav?.next_id}>
            {t('process.review.next')} <ChevronRight />
          </Button>
        </div>
        {actionButtons(true)}
      </div>

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
