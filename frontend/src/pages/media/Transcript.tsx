/* Media page transcript: every sentence plays in place (only that sentence), shows karaoke highlighting while it
 * plays, and can be reviewed (pass / needs fix / exclude) or corrected inline — no navigation. */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ArrowUpRight, AudioWaveform, Check, ChevronDown, CircleSlash, Loader2, MoreHorizontal, Pause, Pencil, Play, TriangleAlert, Wand2 } from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useMediaSegments, useMediaWords, useSegmentPatch } from '@/api/queries'
import type { MediaWordsResponse, ReviewStatus, SegmentSummary } from '@/api/types'
import { fmtSec, fmtTimestamp } from '@/lib/format'
import { flagLabel, reviewStatus, textSource } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'
import { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardHeader, Tag } from '@/components/ui/primitives'
import { Textarea } from '@/components/ui/form'
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { EmptyState, ListSkeleton } from '@/components/ui/states'
import { ReviewBadge } from '@/components/domain/status'
import { speakerName } from '@/components/domain/speakers'
import { KaraokeText, toUnits, useClock, type Unit } from '@/components/domain/karaoke'

/** rows rendered at first; the rest on demand (long podcasts have 1000+ sentences) */
const STEP = 120
type Player = ReturnType<typeof useSentencePlayer>

const STATUS_ACTIONS: { st: ReviewStatus; key: string; icon: typeof Check; on: string }[] = [
  { st: 'passed', key: '1', icon: Check, on: 'bg-success-soft text-success-fg ring-1 ring-success/40' },
  { st: 'needs_fix', key: '2', icon: TriangleAlert, on: 'bg-warning-soft text-warning-fg ring-1 ring-warning/40' },
  { st: 'excluded', key: '3', icon: CircleSlash, on: 'bg-danger-soft text-danger-fg ring-1 ring-danger/40' },
]

/** Word-alignment status chip + action (hidden when the words endpoint is unavailable). */
function AlignStatus({ mediaId, words }: { mediaId: string; words: MediaWordsResponse | undefined }) {
  const t = useT()
  const qc = useQueryClient()
  const align = useMutation({
    mutationFn: () => api.alignMedia(mediaId),
    onSuccess: (r) => {
      toast.success(r.queued ? t('process.media.alignQueued') : t('process.media.alignNothing'))
      qc.invalidateQueries({ queryKey: qk.mediaDetail(mediaId) })
      qc.invalidateQueries({ queryKey: qk.mediaWords(mediaId) })
    },
    onError: onErr,
  })
  if (!words || !words.segments) return null
  const total = words.segments
  const st = words.status
  const done = Math.min(total, st.aligned ?? Object.values(words.words).filter((w) => w?.length).length)
  const skipped = st.skipped ?? 0
  if (done >= total)
    return (
      <Badge tone="success" dot>
        {t('process.media.aligned')}
      </Badge>
    )
  if (words.job_active || (st.pending ?? 0) > 0)
    return (
      <Badge tone="primary">
        <Loader2 className="size-3 animate-spin" /> {t('process.media.aligning')}
        {done > 0 && <span className="tabular opacity-80">{done}/{total}</span>}
      </Badge>
    )
  // everything that could be aligned is: the rest are languages without a romanizer (Taiwanese, Hakka …)
  if (skipped > 0 && done + skipped >= total)
    return (
      <Tooltip content={t('process.media.alignSkippedTip')}>
        <Badge tone="neutral" dot>
          {done ? t('process.media.alignedPartial', done, total) : t('process.media.alignSkipped')}
        </Badge>
      </Tooltip>
    )
  return (
    <span className="inline-flex items-center gap-1.5">
      <Tooltip content={t('process.media.notAlignedTip')}>
        <Badge tone="neutral" dot>
          {done ? t('process.media.alignedPartial', done, total) : t('process.media.notAligned')}
        </Badge>
      </Tooltip>
      <Button size="xs" variant="ghost" onClick={() => align.mutate()} loading={align.isPending}>
        <Wand2 /> {t('process.media.align')}
      </Button>
    </span>
  )
}

/** Sentence text: karaoke when timed units exist, else a sentence-level highlight while active. */
function SentenceText({ s, units, active, player }: { s: SegmentSummary; units: Unit[] | null; active: boolean; player: Player }) {
  const time = useClock(player.clock, active)
  if (units) return <KaraokeText units={units} time={time} className="text-[15px] leading-7 break-cjk" />
  return (
    <span
      className={cn(
        'rounded-[3px] text-[15px] leading-7 break-cjk transition-colors',
        active && 'bg-primary-soft text-primary-soft-fg shadow-[0_0_0_2px_var(--primary-soft)] [box-decoration-break:clone]',
      )}
    >
      {s.text}
    </span>
  )
}

/** Thin progress line along the bottom of the playing row (sentence-level position). */
function RowProgress({ s, player }: { s: SegmentSummary; player: Player }) {
  const time = useClock(player.clock, true)
  const p = time == null ? 0 : Math.max(0, Math.min(1, (time - s.start_sec) / Math.max(0.01, s.end_sec - s.start_sec)))
  return <span aria-hidden className="absolute bottom-0 left-0 h-0.5 bg-primary/70" style={{ width: `${p * 100}%` }} />
}

export interface RowHandle {
  focus: () => void
}

const Row = forwardRef<
  RowHandle,
  { s: SegmentSummary; units: Unit[] | null; player: Player; mediaId: string; onMove: (d: number) => void }
>(function Row({ s, units, player, mediaId, onMove }, ref) {
  const t = useT()
  const patch = useSegmentPatch()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(s.text)
  const rowRef = useRef<HTMLDivElement>(null)
  useImperativeHandle(ref, () => ({ focus: () => rowRef.current?.focus() }), [])
  const active = player.id === s.id && player.status !== 'idle'
  const playing = active && (player.status === 'playing' || player.status === 'loading')

  const setStatus = (st: ReviewStatus) =>
    patch.mutate({ id: s.id, patch: { review_status: s.review_status === st ? 'unchecked' : st } })
  const startEdit = () => {
    setDraft(s.text)
    setEditing(true)
  }
  const save = () => {
    const v = draft.trim()
    if (!v || v === s.text) return setEditing(false)
    patch.mutate({ id: s.id, patch: { corrected_text: v === s.original_text ? null : v } }, { onSuccess: () => toast.success(t('process.media.textSaved'), { duration: 1400 }) })
    setEditing(false)
    rowRef.current?.focus()
  }
  const cancel = () => {
    setEditing(false)
    rowRef.current?.focus()
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget || e.metaKey || e.ctrlKey || e.altKey) return
    const k = e.key.toLowerCase()
    if (k === ' ' || k === 'enter') player.play(s)
    else if (k === '1' || k === '2' || k === '3') setStatus(STATUS_ACTIONS[Number(k) - 1].st)
    else if (k === 'e') startEdit()
    else if (k === 'arrowdown' || k === 'j') onMove(1)
    else if (k === 'arrowup' || k === 'k') onMove(-1)
    else return
    e.preventDefault()
  }
  const reviewHref = `/review/${s.id}?media=${mediaId}&status=all`
  const who = s.speaker ? speakerName(s.speaker) : s.local_speaker

  return (
    <div
      ref={rowRef}
      id={`seg-${s.id}`}
      data-seg-row
      tabIndex={0}
      aria-label={`${fmtTimestamp(s.start_sec)} ${s.text}`}
      aria-keyshortcuts="Space 1 2 3 E"
      onKeyDown={onKey}
      onClick={() => !editing && player.play(s)}
      className={cn(
        'group relative flex min-w-0 cursor-pointer gap-3 px-3 py-2.5 outline-none transition-colors sm:px-5',
        'focus-visible:bg-surface-2/70 focus-visible:shadow-[inset_3px_0_0_var(--ring)]',
        active ? 'bg-primary-soft/40 shadow-[inset_3px_0_0_var(--primary)]' : 'hover:bg-surface-2/60',
        editing && 'cursor-default',
      )}
    >
      {/* play / time */}
      <button
        type="button"
        tabIndex={-1}
        onClick={(e) => (e.stopPropagation(), player.play(s))}
        aria-label={playing ? t('process.media.pauseSentence') : t('process.media.playSentence')}
        className="flex w-12 shrink-0 flex-col items-center gap-0.5 pt-0.5 sm:w-14"
      >
        <span
          className={cn(
            'grid size-8 place-items-center rounded-full transition-colors [&_svg]:size-3.5',
            active ? 'bg-primary text-primary-fg' : 'bg-surface-2 text-fg-2 group-hover:bg-primary-soft group-hover:text-primary-soft-fg',
          )}
        >
          {active && player.status === 'loading' ? <Loader2 className="animate-spin" /> : playing ? <Pause /> : <Play className="translate-x-px" />}
        </span>
        <span className="font-mono text-[10px] leading-4 text-fg-3 sm:text-[11px]">{fmtTimestamp(s.start_sec).replace(/\.\d+$/, '')}</span>
      </button>

      {/* text */}
      <div className="min-w-0 flex-1 pt-0.5">
        {editing ? (
          <div onClick={(e) => e.stopPropagation()} className="space-y-2">
            <Textarea
              autoFocus
              value={draft}
              rows={2}
              onChange={(e) => setDraft(e.target.value)}
              onFocus={(e) => e.currentTarget.setSelectionRange(e.currentTarget.value.length, e.currentTarget.value.length)}
              onKeyDown={(e) => {
                e.stopPropagation()
                if (e.key === 'Escape') (e.preventDefault(), cancel())
                else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) (e.preventDefault(), save())
              }}
              aria-label={t('process.media.editText')}
              className="text-[15px] leading-7"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" variant="primary" onClick={save}>
                {t('process.media.saveText')}
              </Button>
              <Button size="xs" variant="ghost" onClick={cancel}>
                {t('process.media.cancelEdit')}
              </Button>
              <span className="hidden text-[11px] text-fg-3 sm:inline">{t('process.media.editKeys')}</span>
            </div>
          </div>
        ) : (
          <SentenceText s={s} units={units} active={active} player={player} />
        )}
        <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-fg-3">
          {who && <span className="max-w-40 truncate">{who}</span>}
          <span className="tabular">{fmtSec(s.duration)}</span>
          {s.corrected_text && <Tag className="border-success/30 text-success-fg">{textSource.human_revision}</Tag>}
          {s.flags.map((f) => (
            <Tag key={f}>{flagLabel[f] ?? f}</Tag>
          ))}
        </div>
      </div>

      {/* actions ≥ sm */}
      <div className="hidden shrink-0 items-start gap-0.5 sm:flex" onClick={(e) => e.stopPropagation()}>
        <div role="group" aria-label={t('process.media.moreActions')} className="flex items-center gap-0.5 rounded-lg p-0.5">
          {STATUS_ACTIONS.map(({ st, key, icon: Icon, on }) => (
            <Tooltip key={st} content={`${reviewStatus[st].label} (${key})`}>
              <button
                type="button"
                tabIndex={-1}
                aria-pressed={s.review_status === st}
                aria-label={reviewStatus[st].label}
                onClick={() => setStatus(st)}
                className={cn(
                  'grid size-8 place-items-center rounded-md transition-colors [&_svg]:size-4',
                  s.review_status === st ? on : 'text-fg-3 hover:bg-surface-2 hover:text-fg',
                )}
              >
                <Icon />
              </button>
            </Tooltip>
          ))}
        </div>
        <span className="mx-0.5 mt-2 h-4 w-px bg-border" />
        <Tooltip content={`${t('process.media.editText')} (E)`}>
          <button type="button" tabIndex={-1} aria-label={t('process.media.editText')} onClick={startEdit} className="grid size-8 place-items-center rounded-md text-fg-3 hover:bg-surface-2 hover:text-fg [&_svg]:size-4">
            <Pencil />
          </button>
        </Tooltip>
        <Tooltip content={t('process.media.openInReview')}>
          <Link to={reviewHref} tabIndex={-1} aria-label={t('process.media.openInReview')} className="grid size-8 place-items-center rounded-md text-fg-3 hover:bg-surface-2 hover:text-fg [&_svg]:size-4">
            <ArrowUpRight />
          </Link>
        </Tooltip>
      </div>

      {/* phone: status + compact menu */}
      <div className="flex shrink-0 flex-col items-end gap-1 sm:hidden" onClick={(e) => e.stopPropagation()}>
        <Menu>
          <MenuTrigger asChild>
            <button type="button" aria-label={t('process.media.moreActions')} className="grid size-9 place-items-center rounded-md text-fg-2 hover:bg-surface-2 [&_svg]:size-[18px]">
              <MoreHorizontal />
            </button>
          </MenuTrigger>
          <MenuContent>
            {STATUS_ACTIONS.map(({ st, icon: Icon }) => (
              <MenuItem key={st} icon={<Icon />} onSelect={() => setStatus(st)} shortcut={s.review_status === st ? '✓' : undefined}>
                {reviewStatus[st].label}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem icon={<Pencil />} onSelect={startEdit}>
              {t('process.media.editText')}
            </MenuItem>
            <MenuItem icon={<ArrowUpRight />} onSelect={() => navigate(reviewHref)}>
              {t('process.media.openInReview')}
            </MenuItem>
          </MenuContent>
        </Menu>
        {s.review_status !== 'unchecked' && <ReviewBadge status={s.review_status} className="h-5 px-1.5 text-[11px]" />}
      </div>

      {active && !units && <RowProgress s={s} player={player} />}
    </div>
  )
})

export function TranscriptCard({ mediaId, player, focusId }: { mediaId: string; player: Player; focusId?: string | null }) {
  const t = useT()
  const [shown, setShown] = useState(STEP)
  const segs = useMediaSegments(mediaId)
  const words = useMediaWords(mediaId, !!segs.data?.total)
  const all = segs.data?.items ?? []
  // keep a sentence played from the timeline renderable
  const focusIdx = focusId ? all.findIndex((x) => x.id === focusId) : -1
  const limit = Math.max(shown, focusIdx + 20)
  const rows = all.slice(0, limit)
  const refs = useRef<(RowHandle | null)[]>([])
  const move = (i: number, d: number) => refs.current[Math.max(0, Math.min(rows.length - 1, i + d))]?.focus()

  // a click on the timeline plays a sentence: bring its row into view when it is on this page
  useEffect(() => {
    if (!focusId) return
    void document.getElementById(`seg-${focusId}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [focusId])

  return (
    <Card className="overflow-clip">
      <CardHeader
        title={t('process.media.transcript')}
        description={t('process.media.transcriptDesc')}
        actions={
          <Button asChild size="sm" variant="soft">
            <Link to={`/review?media=${mediaId}`}>
              <AudioWaveform /> {t('process.media.reviewThis')}
            </Link>
          </Button>
        }
      />
      {segs.data && segs.data.total > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 pt-2 text-xs text-fg-3 sm:px-5">
          <span className="tabular">{t('process.media.segmentCount', segs.data.total)}</span>
          <AlignStatus mediaId={mediaId} words={words.data} />
          <span className="ml-auto hidden lg:inline">{t('process.media.rowKeys')}</span>
        </div>
      )}
      <div className="mt-3 border-t border-border">
        {!segs.data ? (
          <ListSkeleton rows={6} />
        ) : !rows.length ? (
          <EmptyState compact icon={<AudioWaveform />} title={t('process.media.noSegments')} description={t('process.media.noSegmentsDesc')} />
        ) : (
          <>
            <div className="divide-y divide-border">
              {rows.map((s, i) => (
                <Row
                  key={s.id}
                  ref={(h) => {
                    refs.current[i] = h
                  }}
                  s={s}
                  units={toUnits(words.data?.words[s.id], s.start_sec, s.end_sec)}
                  player={player}
                  mediaId={mediaId}
                  onMove={(d) => move(i, d)}
                />
              ))}
            </div>
            {all.length > rows.length && (
              <div className="border-t border-border p-3 text-center">
                <Button variant="ghost" onClick={() => setShown(limit + STEP * 2)}>
                  <ChevronDown /> {t('process.media.showMore', all.length - rows.length)}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  )
}
