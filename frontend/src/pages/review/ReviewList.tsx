/* /review — level 1: the filtered list of segments to review. Clicking a row opens the per-segment reviewer
 * (/review/:id) with the same query string; "back to list" returns here at the same scroll position. */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { ArrowRight, Check, ListFilter, Loader2, Pause, Play, RotateCcw, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { qk, useChannels, useSegment, useSegments } from '@/api/queries'
import type { SegmentSummary } from '@/api/types'
import { fmtDb, fmtMos, fmtSec, fmtTimestamp } from '@/lib/format'
import { flagLabel, textSource } from '@/lib/labels'
import { OcrSourceBadge, OcrUnverifiedTag, isOcrSource, isOcrUnverified } from '@/components/domain/ocr'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'
import { useIsDesktop } from '@/hooks/useMedia'
import { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { KaraokeText, SeekableChars, timeAtChar, toUnits, useClock } from '@/components/domain/karaoke'
import { SpeakerDot, localSpeakerName, speakerVars, useSpeakerIndexMany } from '@/components/domain/speaker-color'
import { speakerName } from '@/components/domain/speakers'
import { Page, PageHeader, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, Tag } from '@/components/ui/primitives'
import { Chip, NumberInput, SearchInput, Segmented, Select } from '@/components/ui/form'
import { Sheet } from '@/components/ui/overlay'
import { Pagination } from '@/components/ui/data-table'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { ReviewBadge } from '@/components/domain/status'
import { ValidityBadge } from '@/components/domain/validity'
import { LIST_LIMIT, recallListScroll, rememberListScroll, takeListReturn, toSegmentQuery, useReviewParams, type ReviewNavState, type ReviewParams } from './params'

const FLAGS = ['low_snr', 'low_speech', 'clipped', 'repeated', 'overlap', 'too_short', 'boundary_adjusted', 's2t_converted']

function activeFilterCount(p: ReviewParams) {
  return (p.channel ? 1 : 0) + (p.source ? 1 : 0) + (p.validity ? 1 : 0) + (p.flags.length ? 1 : 0) + (p.snr !== null ? 1 : 0) + (p.mos !== null ? 1 : 0) + (p.sort ? 1 : 0)
}

function StatusFilter({ p, set }: { p: ReviewParams; set: ReturnType<typeof useReviewParams>['set'] }) {
  const t = useT()
  return (
    <div className="-mx-1 min-w-0 overflow-x-auto px-1 no-scrollbar">
      <Segmented
        aria-label={t('process.review.statusFilter')}
        value={p.status}
        onChange={(v) => set({ status: v === 'unchecked' ? null : v })}
        options={[
          { value: 'unchecked', label: t('process.review.statusUnchecked') },
          { value: 'needs_fix', label: t('process.review.statusFix') },
          { value: 'passed', label: t('process.review.statusPassed') },
          { value: 'excluded', label: t('process.review.statusExcluded') },
          { value: 'all', label: t('process.review.statusAll') },
        ]}
      />
    </div>
  )
}

/** Secondary filters (inline on desktop, in a sheet below xl). */
function MoreFilters({ p, set, stacked }: { p: ReviewParams; set: ReturnType<typeof useReviewParams>['set']; stacked?: boolean }) {
  const t = useT()
  const ch = useChannels()
  return (
    <div className={cn('flex gap-3', stacked ? 'flex-col' : 'flex-wrap items-end')}>
      <label className={cn('min-w-0', stacked ? 'space-y-1.5' : 'w-48 space-y-1')}>
        <span className="text-xs font-medium text-fg-3">{t('process.review.channel')}</span>
        <Select value={p.channel} onChange={(e) => set({ channel: e.target.value, media: null })}>
          <option value="">{t('process.review.allChannels')}</option>
          {ch.data?.items.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </Select>
      </label>
      <label className={cn('min-w-0', stacked ? 'space-y-1.5' : 'w-40 space-y-1')}>
        <span className="text-xs font-medium text-fg-3">{t('process.review.textSource')}</span>
        <Select value={p.source} onChange={(e) => set({ source: e.target.value })}>
          <option value="">{t('process.review.allSources')}</option>
          <option value="manual_caption">{textSource.manual_caption}</option>
          <option value="auto_caption">{textSource.auto_caption}</option>
          <option value="ocr_caption">{textSource.ocr_caption}</option>
          <option value="human_revision">{textSource.human_revision}</option>
        </Select>
      </label>
      <label className={cn('min-w-0', stacked ? 'space-y-1.5' : 'w-36 space-y-1')}>
        <span className="text-xs font-medium text-fg-3">{t('validate.filter.label')}</span>
        <Select value={p.validity} onChange={(e) => set({ validity: e.target.value })} data-shot="validity-filter">
          <option value="">{t('validate.filter.all')}</option>
          <option value="valid">{t('validate.filter.valid')}</option>
          <option value="invalid">{t('validate.filter.invalid')}</option>
          <option value="unknown">{t('validate.filter.unknown')}</option>
        </Select>
      </label>
      <div className={cn('grid grid-cols-2 gap-2', stacked ? '' : 'w-64')}>
        <label className="min-w-0 space-y-1">
          <span className="text-xs font-medium text-fg-3">{t('process.review.minSnr')}</span>
          <NumberInput value={p.snr} onChange={(v) => set({ snr: v })} step={1} suffix="dB" />
        </label>
        <label className="min-w-0 space-y-1">
          <span className="text-xs font-medium text-fg-3">{t('process.review.minMos')}</span>
          <NumberInput value={p.mos} onChange={(v) => set({ mos: v })} step={0.1} min={1} max={5} />
        </label>
      </div>
      <label className={cn('min-w-0', stacked ? 'space-y-1.5' : 'w-52 space-y-1')}>
        <span className="text-xs font-medium text-fg-3">{t('process.review.sort')}</span>
        <Select value={p.sort} onChange={(e) => set({ sort: e.target.value })}>
          <option value="">{t('process.review.sortDefault')}</option>
          <option value="snr">{t('process.review.sortSnrAsc')}</option>
          <option value="-snr">{t('process.review.sortSnrDesc')}</option>
          <option value="dnsmos">{t('process.review.sortMosAsc')}</option>
          <option value="-duration">{t('process.review.sortLongFirst')}</option>
          <option value="-updated_at">{t('process.review.sortRecent')}</option>
        </Select>
      </label>
      <div className={cn('min-w-0 space-y-1.5', !stacked && 'basis-full')}>
        <span className="block text-xs font-medium text-fg-3">{t('process.review.flags')}</span>
        <div className="flex flex-wrap gap-1.5">
          {FLAGS.map((f) => (
            <Chip
              key={f}
              active={p.flags.includes(f)}
              onClick={() => set({ flags: (p.flags.includes(f) ? p.flags.filter((x) => x !== f) : [...p.flags, f]).join(',') })}
            >
              {flagLabel[f] ?? f}
            </Chip>
          ))}
        </div>
      </div>
    </div>
  )
}

function QualityChips({ s }: { s: SegmentSummary }) {
  const low = (s.snr ?? 99) < 10
  const lowMos = (s.dnsmos_ovrl ?? 5) < 2.5
  return (
    <span className="flex flex-wrap items-center gap-1 text-[11px] tabular">
      <span className={cn('rounded px-1.5 py-0.5', low ? 'bg-warning-soft text-warning-fg' : 'bg-surface-2 text-fg-2')}>SNR {fmtDb(s.snr)}</span>
      <span className={cn('rounded px-1.5 py-0.5', lowMos ? 'bg-warning-soft text-warning-fg' : 'bg-surface-2 text-fg-2')}>MOS {fmtMos(s.dnsmos_ovrl)}</span>
    </span>
  )
}

type Player = ReturnType<typeof useSentencePlayer>

/** The playing row's text: per-character karaoke from the segment's word timings (fetched for this row only),
 * or a sentence-level highlight when the segment has no alignment. Characters seek (click / tap). */
function PlayingText({ s, player }: { s: SegmentSummary; player: Player }) {
  const detail = useSegment(s.id)
  const units = toUnits(detail.data?.words ?? null, s.start_sec, s.end_sec)
  const time = useClock(player.clock, true)
  if (units) return <KaraokeText units={units} time={time} onSeek={(at) => player.play(s, at)} />
  return <span className="hl-sentence rounded-[3px]">{s.text}</span>
}

/** A row's speaker: colour dot + 語者 N (+ the candidate speaker's name when it has one). */
function rowSpeaker(s: SegmentSummary, idx: number | null) {
  if (idx == null) return null
  const name = localSpeakerName(idx)
  return {
    label: s.speaker?.display_name ? `${name} · ${s.speaker.display_name}` : name,
    title: [s.local_speaker, s.speaker ? speakerName(s.speaker) : null].filter(Boolean).join(' · '),
  }
}

function SegmentRow({
  s,
  href,
  onOpen,
  player,
  focused,
  speakerIdx,
}: {
  s: SegmentSummary
  href: string
  onOpen: () => void
  player: Player
  focused: boolean
  speakerIdx: number | null
}) {
  const t = useT()
  const qc = useQueryClient()
  const active = player.id === s.id && player.status !== 'idle'
  const playing = active && player.status !== 'paused'
  const who = rowSpeaker(s, speakerIdx)
  // a click on a character of a row that is not playing: fetch its word timings, play from that character
  const playFromChar = async (charIdx: number) => {
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
  return (
    <li
      id={`rv-${s.id}`}
      data-speaker={speakerIdx ?? undefined}
      style={speakerVars(speakerIdx)}
      className={cn(
        'relative flex min-w-0 items-start gap-2 px-3 py-3 transition-colors sm:gap-3 sm:px-4 md:items-center md:gap-4',
        active ? 'hl-row-active' : 'hover:bg-surface-2/60',
        focused && 'animate-flash',
      )}
    >
      <button
        type="button"
        onClick={() => player.play(s)}
        aria-label={playing ? t('process.review.stopSample') : t('process.review.playSample')}
        className={cn(
          'mt-0.5 grid size-9 shrink-0 place-items-center rounded-full transition-colors md:mt-0 [&_svg]:size-3.5',
          playing ? 'bg-primary text-primary-fg' : 'bg-surface-2 text-fg-2 hover:bg-primary-soft hover:text-primary-soft-fg',
        )}
      >
        {active && player.status === 'loading' ? <Loader2 className="animate-spin" /> : playing ? <Pause /> : <Play className="translate-x-px" />}
      </button>
      <Link
        to={href}
        state={{ fromList: true } satisfies ReviewNavState}
        onClick={onOpen}
        className="grid min-w-0 flex-1 gap-x-4 gap-y-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_4rem_10rem_6.5rem] md:items-center"
      >
        <span className="min-w-0">
          <span
            data-row-text
            title={t('process.media.clickCharHint')}
            className={cn('line-clamp-2 text-[14px] leading-6 break-cjk', s.validity === 'invalid' ? 'text-fg-3' : 'text-fg', active && 'line-clamp-none')}
          >
            {active ? <PlayingText s={s} player={player} /> : <SeekableChars text={s.text} onChar={(i) => void playFromChar(i)} />}
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-fg-3">
            {who && (
              <>
                <SpeakerDot index={speakerIdx} label={who.label} title={who.title} className="max-w-36 shrink-0" />
                <span className="shrink-0">·</span>
              </>
            )}
            <span className="truncate">{s.media_title}</span>
            <span className="shrink-0">·</span>
            <span className="shrink-0 truncate">{s.channel_title}</span>
            <span className="hidden shrink-0 font-mono sm:inline">· {fmtTimestamp(s.start_sec).replace(/\.\d+$/, '')}</span>
          </span>
          {(s.flags.length > 0 || s.text_source === 'auto_caption' || isOcrSource(s) || s.validity === 'invalid' || s.validity_source === 'manual') && (
            <span className="mt-1 flex flex-wrap gap-1">
              <ValidityBadge s={s} className="h-auto min-h-5 text-[11px]" />
              {s.text_source === 'auto_caption' && <Tag className="border-warning/30 text-warning-fg">{textSource.auto_caption}</Tag>}
              {isOcrSource(s) && <OcrSourceBadge tag />}
              {isOcrUnverified(s) && <OcrUnverifiedTag />}
              {s.flags.map((f) => (
                <Tag key={f}>{flagLabel[f] ?? f}</Tag>
              ))}
            </span>
          )}
        </span>
        <span className="hidden text-right text-xs tabular text-fg-2 md:block">{fmtSec(s.duration)}</span>
        <span className="hidden md:block">
          <QualityChips s={s} />
        </span>
        <span className="hidden justify-end md:flex">
          <ReviewBadge status={s.review_status} />
        </span>
        {/* phone meta */}
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 md:hidden">
          <ReviewBadge status={s.review_status} />
          <span className="text-[11px] tabular text-fg-3">{fmtSec(s.duration)}</span>
          <QualityChips s={s} />
        </span>
      </Link>
    </li>
  )
}

export default function ReviewList() {
  const t = useT()
  useTitle(t('process.review.title'))
  const { p, set, setAll, sp } = useReviewParams()
  const navigate = useNavigate()
  const location = useLocation()
  const isDesktop = useIsDesktop()
  const [sheet, setSheet] = useState(false)
  const [qDraft, setQDraft] = useState(p.q)
  const player = useSentencePlayer()
  const list = useSegments({ ...toSegmentQuery(p), limit: LIST_LIMIT, offset: p.offset })
  const media = p.media ? list.data?.items[0]?.media_title : undefined
  const spIdx = useSpeakerIndexMany((list.data?.items ?? []).map((x) => x.media_id))
  // coming back from the reviewer through history: it may have moved on to another page of the list
  const [ret] = useState(takeListReturn)
  const waiting = !!ret && ret.search !== sp.toString()
  useEffect(() => {
    if (waiting) setAll(ret!.search)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const focusId = (location.state as ReviewNavState | null)?.focus ?? ret?.focus ?? null
  const restored = useRef(false)

  useEffect(() => {
    const id = setTimeout(() => qDraft !== p.q && set({ q: qDraft }), 300)
    return () => clearTimeout(id)
  }, [qDraft]) // eslint-disable-line react-hooks/exhaustive-deps

  // restore the reader's place once rows are on screen (after the shell's scroll-to-top on navigation)
  useLayoutEffect(() => {
    if (restored.current || !list.data || list.isPlaceholderData || (ret && ret.search !== sp.toString())) return
    restored.current = true
    const y = recallListScroll(sp)
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const el = focusId ? document.getElementById(`rv-${focusId}`) : null
        if (el) el.scrollIntoView({ block: 'center' })
        else if (y !== null) window.scrollTo({ top: y })
      }),
    )
  }, [list.data, list.isPlaceholderData, sp.toString()]) // eslint-disable-line react-hooks/exhaustive-deps

  // legacy deep links: /review?segment=<id>&… → /review/<id>?…
  const legacy = sp.get('segment')
  if (legacy) {
    const n = new URLSearchParams(sp)
    n.delete('segment')
    return <Navigate to={`/review/${legacy}?${n}`} replace />
  }

  const href = (id: string) => `/review/${id}${sp.toString() ? `?${sp}` : ''}`
  const open = () => rememberListScroll(sp)
  const items = list.data?.items ?? []
  const nFilters = activeFilterCount(p)

  return (
    <Page wide>
      <PageHeader
        title={t('process.review.title')}
        description={t('process.review.listDesc')}
        actions={
          items[0] && (
            <Button variant="primary" onClick={() => (open(), navigate(href(items[0].id), { state: { fromList: true } satisfies ReviewNavState }))}>
              {t('process.review.startFirst')} <ArrowRight />
            </Button>
          )
        }
      />

      <Card className="mb-4 p-3 sm:p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <StatusFilter p={p} set={set} />
          <div className="flex min-w-0 flex-1 gap-2">
            <SearchInput value={qDraft} onChange={setQDraft} placeholder={t('process.review.searchPlaceholder')} className="min-w-0 flex-1" />
            {!isDesktop && (
              <Button onClick={() => setSheet(true)} className="shrink-0">
                <ListFilter /> <span className="hidden sm:inline">{t('process.review.filters')}</span>
                {nFilters > 0 && <span className="grid size-5 place-items-center rounded-full bg-primary text-[11px] text-primary-fg">{nFilters}</span>}
              </Button>
            )}
          </div>
        </div>
        {isDesktop && (
          <div className="mt-4 border-t border-border pt-4">
            <MoreFilters p={p} set={set} />
          </div>
        )}
      </Card>

      <div className="mb-2 flex min-h-8 flex-wrap items-center gap-2 px-1 text-[13px] text-fg-3">
        <span className="tabular">{list.data ? t('process.review.results', list.data.total) : t('process.review.loadingQueue')}</span>
        {p.media && (
          <span className="inline-flex h-8 max-w-full items-center gap-1 rounded-full border border-primary/25 bg-primary-soft pl-3 pr-1 text-xs font-medium text-primary-soft-fg">
            <span className="truncate">{media ? t('process.review.videoChip', media) : t('process.review.thisVideo')}</span>
            <button aria-label={t('process.review.removeVideoFilter')} className="grid size-6 shrink-0 place-items-center rounded-full hover:bg-primary/15" onClick={() => set({ media: null })}>
              <X className="size-3" />
            </button>
          </span>
        )}
        {nFilters > 0 && (
          <button className="h-8 px-2 text-xs hover:text-fg" onClick={() => set({ channel: null, source: null, validity: null, flags: null, snr: null, mos: null, sort: null })}>
            {t('process.review.resetFilters')}
          </button>
        )}
      </div>

      <Card className="overflow-clip">
        {list.isError ? (
          <ErrorState error={list.error} onRetry={() => list.refetch()} />
        ) : !list.data ? (
          <ListSkeleton rows={8} />
        ) : !items.length ? (
          <EmptyState
            icon={p.status === 'unchecked' && !nFilters && !p.q ? <Check /> : <ListFilter />}
            title={p.status === 'unchecked' && !nFilters && !p.q ? t('process.review.emptyTitle') : t('process.review.noMatchTitle')}
            description={p.status === 'unchecked' && !nFilters && !p.q ? t('process.review.emptyDesc') : t('process.review.noMatchDesc')}
            action={
              <Button onClick={() => (setQDraft(''), set({ status: 'all', channel: null, media: null, source: null, validity: null, flags: null, snr: null, mos: null, q: null, sort: null }))}>
                <RotateCcw /> {t('process.review.showAll')}
              </Button>
            }
          />
        ) : (
          <div className={cn('transition-opacity', list.isFetching && 'opacity-60')}>
            <div className="hidden grid-cols-[2.25rem_minmax(0,1fr)_4rem_10rem_6.5rem] gap-x-4 border-b border-border bg-surface-2/70 px-4 py-2 text-xs font-medium text-fg-3 md:grid">
              <span />
              <span>{t('process.review.colText')}</span>
              <span className="text-right">{t('process.review.colDuration')}</span>
              <span>{t('process.review.colQuality')}</span>
              <span className="text-right">{t('process.review.colStatus')}</span>
            </div>
            <ul className="divide-y divide-border">
              {items.map((s) => (
                <SegmentRow key={s.id} s={s} href={href(s.id)} onOpen={open} player={player} focused={s.id === focusId} speakerIdx={spIdx(s.media_id, s.local_speaker)} />
              ))}
            </ul>
            <Pagination total={list.data.total} limit={LIST_LIMIT} offset={p.offset} onChange={(o) => (set({ offset: o || null }), window.scrollTo({ top: 0 }))} className="border-t border-border" />
          </div>
        )}
      </Card>

      <Sheet open={sheet} onOpenChange={setSheet} title={t('process.review.filters')} description={list.data ? t('process.review.results', list.data.total) : undefined}>
        <div className="p-4">
          <MoreFilters p={p} set={set} stacked />
        </div>
        <div className="sticky bottom-0 flex gap-2 border-t border-border bg-surface p-3">
          <Button className="flex-1" onClick={() => set({ channel: null, source: null, validity: null, flags: null, snr: null, mos: null, sort: null })}>
            {t('process.review.resetFilters')}
          </Button>
          <Button className="flex-1" variant="primary" onClick={() => setSheet(false)}>
            {list.data ? t('process.review.results', list.data.total) : t('common.close')}
          </Button>
        </div>
      </Sheet>
    </Page>
  )
}
