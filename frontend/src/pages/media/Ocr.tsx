/* Media page: hard-subtitle OCR panel — status, summary, rejected-by-reason chips and a browsable list of subtitle
 * events with their crops. Clicking an event plays its time range (shared sentence player, full media audio). */
import { useMemo, useState, type KeyboardEvent } from 'react'
import { AlertTriangle, ChevronDown, Info, Loader2, Pause, Play, RotateCw, ScanText } from 'lucide-react'
import { useMediaOcr, useRunOcr } from '@/api/queries'
import type { MediaDetail, OcrEvent, OcrResult, OcrSummary } from '@/api/types'
import { fmtInt, fmtPct, fmtRelative, fmtSec, fmtTimestamp } from '@/lib/format'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'
import { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, Skeleton, Tag } from '@/components/ui/primitives'
import { Segmented } from '@/components/ui/form'
import { Dialog, Tooltip } from '@/components/ui/overlay'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { useClock } from '@/components/domain/karaoke'
import { OcrCrop, OcrStatusBadge, eventSpan, ocrReason, ocrReasonTip } from '@/components/domain/ocr'

type Player = ReturnType<typeof useSentencePlayer>
type Filter = 'all' | 'paired' | 'rejected'
/** rows rendered at first; long videos have several hundred events */
const STEP = 60
const REASON_ORDER = ['no_speech', 'little_speech', 'rate_mismatch', 'static_text', 'concurrent_tracks', 'low_ocr_confidence', 'too_short_text', 'too_long', 'too_short']

export const ocrKey = (e: Pick<OcrEvent, 'idx'>) => `ocr:${e.idx}`

/** The media could be OCRed (YouTube, audio already downloaded, not in the download queue). */
export function canRunOcr(m: MediaDetail) {
  if (m.platform !== 'youtube') return false
  if (m.status === 'queued' || m.status === 'downloading') return false
  return m.downloaded ?? ['downloaded', 'processing', 'ready'].includes(m.status)
}
/** The media has an OCR result or request worth a full panel. */
export const hasOcr = (m: MediaDetail) => !!m.ocr || !!m.ocr_job_active || m.tracks.some((tr) => tr.kind === 'ocr')

/* ───────────── run / re-run with confirmation ───────────── */
export function RunOcrButton({
  m,
  videoAvailable,
  size = 'sm',
  variant,
}: {
  m: MediaDetail
  videoAvailable?: boolean
  size?: 'sm' | 'md'
  variant?: 'primary' | 'secondary' | 'soft'
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const run = useRunOcr(m.id)
  const rerun = hasOcrResult(m)
  const hasCaptions = m.tracks.some((tr) => tr.kind !== 'ocr')
  if (m.ocr_job_active)
    return (
      <Button size={size} variant={variant ?? 'secondary'} disabled data-shot="ocr-run">
        <Loader2 className="animate-spin" /> {t('ocr.running')}
      </Button>
    )
  return (
    <>
      <Button size={size} variant={variant ?? (rerun ? 'secondary' : 'primary')} onClick={() => setOpen(true)} data-shot="ocr-run">
        {rerun ? <RotateCw /> : <ScanText />} {rerun ? t('ocr.rerun') : t('ocr.run')}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        size="sm"
        title={rerun ? t('ocr.confirm.rerunTitle') : t('ocr.confirm.runTitle')}
        description={rerun ? t('ocr.confirm.rerunDesc') : t('ocr.confirm.runDesc')}
        footer={
          <>
            <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
            <Button variant="primary" loading={run.isPending} onClick={() => run.mutate(rerun, { onSuccess: () => setOpen(false) })}>
              <ScanText /> {t('ocr.confirm.start')}
            </Button>
          </>
        }
      >
        <ul className="space-y-2 text-[13px] leading-5 text-fg-2">
          {rerun && videoAvailable !== undefined && (
            <li className="flex gap-2">
              <Info className="mt-0.5 size-4 shrink-0 text-fg-3" />
              <span className="break-cjk">{videoAvailable ? t('ocr.confirm.videoKept') : t('ocr.confirm.videoGone')}</span>
            </li>
          )}
          {hasCaptions && (
            <li className="flex gap-2 text-warning-fg">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <span className="break-cjk">{t('ocr.confirm.hasCaptions')}</span>
            </li>
          )}
        </ul>
      </Dialog>
    </>
  )
}
const hasOcrResult = (m: MediaDetail) => m.tracks.some((tr) => tr.kind === 'ocr') || (!!m.ocr && m.ocr.status !== 'pending') || (m.ocr?.events ?? 0) > 0

/** Sidebar card for a downloaded media that has never been OCRed. */
export function OcrStartCard({ m }: { m: MediaDetail }) {
  const t = useT()
  return (
    <Card data-shot="media-ocr-start">
      <CardHeader icon={<ScanText />} title={t('ocr.card.title')} />
      <CardBody className="space-y-3">
        <div>
          <div className="text-[13px] font-medium">{t('ocr.empty.title')}</div>
          <p className="mt-0.5 text-xs leading-5 text-fg-3 break-cjk">{t('ocr.empty.desc')}</p>
        </div>
        <RunOcrButton m={m} variant="secondary" />
      </CardBody>
    </Card>
  )
}

/* ───────────── summary ───────────── */
function Summary({ s, data, reason, onReason }: { s: OcrSummary; data: OcrResult | undefined; reason: string | null; onReason: (r: string | null) => void }) {
  const t = useT()
  const events = s.events ?? data?.total ?? 0
  const paired = s.paired ?? data?.events.filter((e) => e.status === 'paired').length ?? 0
  const rejectedMap: Record<string, number> = { ...(s.rejected ?? {}) } as Record<string, number>
  if (!Object.keys(rejectedMap).length && data) for (const e of data.events) if (e.status === 'rejected' && e.reason) rejectedMap[e.reason] = (rejectedMap[e.reason] ?? 0) + 1
  const rejected = Object.values(rejectedMap).reduce((a, x) => a + (x ?? 0), 0)
  const reasons = Object.entries(rejectedMap)
    .filter(([, n]) => (n ?? 0) > 0)
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0) || REASON_ORDER.indexOf(a[0]) - REASON_ORDER.indexOf(b[0]))
  const st = s.stats
  const reusedShare = st?.tracking_frames ? (st.reused_frames ?? 0) / st.tracking_frames : null
  const tiles: [string, string, string?][] = [
    [t('ocr.stat.events'), fmtInt(events), s.tracks ? `${t('ocr.stat.tracks')} ${fmtInt(s.tracks)}` : undefined],
    [t('ocr.stat.paired'), fmtInt(paired), events ? fmtPct(paired / events) : undefined],
    [t('ocr.stat.rejected'), fmtInt(rejected), events ? fmtPct(rejected / events) : undefined],
    [t('ocr.stat.pairedSec'), s.paired_sec != null ? fmtSec(s.paired_sec, 0) : '—', reusedShare != null ? `${t('ocr.stat.reused')} ${fmtPct(reusedShare)}` : undefined],
  ]
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {tiles.map(([k, v, h]) => (
          <div key={k} className="min-w-0 rounded-lg bg-surface-2/70 px-3 py-2">
            <div className="truncate text-[11px] text-fg-3">{k}</div>
            <div className="text-[15px] font-semibold leading-6 tabular">{v}</div>
            {h && <div className="truncate text-[11px] text-fg-3">{h}</div>}
          </div>
        ))}
      </div>
      {reasons.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs font-medium text-fg-3">{t('ocr.rejectedBy')}</div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('ocr.rejectedBy')}>
            {reasons.map(([r, n]) => (
              <Tooltip key={r} content={ocrReasonTip(r) || ocrReason(r)}>
                <button
                  type="button"
                  aria-pressed={reason === r}
                  data-ocr-reason={r}
                  onClick={() => onReason(reason === r ? null : r)}
                  className={cn(
                    'inline-flex h-8 max-w-full items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors sm:h-7',
                    reason === r ? 'border-warning/50 bg-warning-soft text-warning-fg' : 'border-border-strong text-fg-2 hover:bg-surface-2',
                  )}
                >
                  <span className="truncate">{ocrReason(r)}</span>
                  <span className="tabular opacity-75">{fmtInt(n ?? 0)}</span>
                </button>
              </Tooltip>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ───────────── event row ───────────── */
function RowProgress({ e, player }: { e: OcrEvent; player: Player }) {
  const time = useClock(player.clock, true)
  const [a, b] = eventSpan(e)
  const p = time == null ? 0 : Math.max(0, Math.min(1, (time - a) / Math.max(0.01, b - a)))
  return <span aria-hidden className="absolute bottom-0 left-0 h-0.5 bg-primary" style={{ width: `${p * 100}%` }} />
}

function EventRow({ e, mediaId, player }: { e: OcrEvent; mediaId: string; player: Player }) {
  const t = useT()
  const key = ocrKey(e)
  const active = player.id === key && player.status !== 'idle'
  const playing = active && (player.status === 'playing' || player.status === 'loading')
  const [a, b] = eventSpan(e)
  const play = () => player.playRange(key, mediaId, a, b)
  const onKey = (ev: KeyboardEvent) => {
    if (ev.target !== ev.currentTarget) return
    if (ev.key === ' ' || ev.key === 'Enter') (ev.preventDefault(), play())
  }
  const rejected = e.status === 'rejected'
  return (
    <div
      tabIndex={0}
      role="button"
      data-ocr-event={e.idx}
      aria-label={`${fmtTimestamp(e.start)} ${e.text}`}
      onClick={play}
      onKeyDown={onKey}
      className={cn(
        'group relative flex min-w-0 cursor-pointer gap-3 px-3 py-2.5 outline-none transition-colors sm:px-5',
        'focus-visible:bg-surface-2/70 focus-visible:shadow-[inset_3px_0_0_var(--ring)]',
        active ? 'hl-row-active' : 'hover:bg-surface-2/60',
      )}
    >
      <span className="flex w-12 shrink-0 flex-col items-center gap-0.5 pt-0.5 sm:w-14">
        <span
          aria-hidden
          className={cn(
            'grid size-8 place-items-center rounded-full transition-colors [&_svg]:size-3.5',
            active ? 'bg-primary text-primary-fg' : 'bg-surface-2 text-fg-2 group-hover:bg-primary-soft group-hover:text-primary-soft-fg',
          )}
        >
          {active && player.status === 'loading' ? <Loader2 className="animate-spin" /> : playing ? <Pause /> : <Play className="translate-x-px" />}
        </span>
        <span className="font-mono text-[10px] leading-4 text-fg-3 sm:text-[11px]">{fmtTimestamp(e.start).replace(/\.\d+$/, '')}</span>
      </span>

      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex min-w-0 flex-col gap-2 lg:flex-row lg:items-center lg:gap-4">
          <OcrCrop src={e.crop_url} alt={t('ocr.event.cropAlt', e.raw_text)} className="h-11 w-full max-w-[22rem] shrink-0 lg:w-[22rem]" />
          <div className="min-w-0 flex-1">
            <p className={cn('text-[15px] leading-6 break-cjk', rejected && 'text-fg-3')}>{e.text || e.raw_text}</p>
            {e.raw_text && e.raw_text !== e.text && (
              <p className="mt-0.5 text-[11px] leading-4 text-fg-3 break-cjk">
                {t('ocr.event.raw')}：{e.raw_text}
              </p>
            )}
          </div>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-fg-3">
          {rejected ? (
            <Tooltip content={ocrReasonTip(e.reason) || ocrReason(e.reason)}>
              <Badge tone="warning" className="h-5 px-1.5 text-[11px]" data-ocr-status="rejected">
                {e.reason ? ocrReason(e.reason) : t('ocr.event.rejected')}
              </Badge>
            </Tooltip>
          ) : (
            <Badge tone="success" dot className="h-5 px-1.5 text-[11px]" data-ocr-status="paired">
              {t('ocr.event.paired')}
            </Badge>
          )}
          <span className="font-mono">
            {fmtTimestamp(e.start)}–{fmtTimestamp(e.end)}
          </span>
          <Tooltip content={t('ocr.event.confTip', e.conf, e.agreement, e.n_frames)}>
            <span className={cn('tabular', e.conf < 0.85 && 'font-medium text-warning-fg')}>{t('ocr.event.conf', e.conf)}</span>
          </Tooltip>
          <span className="hidden tabular sm:inline">{t('ocr.event.frames', e.n_frames)}</span>
          {e.speech_sec > 0 && <span className="tabular">{t('ocr.event.speech', e.speech_sec.toFixed(1))}</span>}
          {e.lines > 1 && <Tag>{t('ocr.event.lines', e.lines)}</Tag>}
          {e.s2t_converted && <Tag className="border-info/30 text-info-fg">{t('ocr.event.s2t')}</Tag>}
        </div>
      </div>
      {active && <RowProgress e={e} player={player} />}
    </div>
  )
}

/* ───────────── card ───────────── */
export function OcrCard({ m, player }: { m: MediaDetail; player: Player }) {
  const t = useT()
  const q = useMediaOcr(m.id)
  const [filter, setFilter] = useState<Filter>('all')
  const [reason, setReason] = useState<string | null>(null)
  const [shown, setShown] = useState(STEP)
  const data = q.data
  const summary: OcrSummary = { ...m.ocr, ...data?.summary, status: data?.summary?.status ?? m.ocr?.status ?? 'pending' }
  const status = data?.status ?? m.ocr?.status ?? 'none'
  const active = !!(data?.job_active ?? m.ocr_job_active)
  const events = useMemo(() => data?.events ?? [], [data])
  const counts = useMemo(() => ({ all: events.length, paired: events.filter((e) => e.status === 'paired').length, rejected: events.filter((e) => e.status === 'rejected').length }), [events])
  const list = useMemo(
    () => events.filter((e) => (reason ? e.status === 'rejected' && e.reason === reason : filter === 'all' || e.status === filter)),
    [events, filter, reason],
  )
  const rows = list.slice(0, shown)
  const pickReason = (r: string | null) => {
    setReason(r)
    if (r) setFilter('rejected')
    setShown(STEP)
  }
  const meta = [
    summary.requested ? t(`ocr.requested.${summary.requested}`) : null,
    summary.model || data?.model ? `${t('ocr.stat.model')} ${summary.model ?? data?.model}${summary.device ? ` · ${summary.device}` : ''}` : null,
    summary.fps ?? data?.fps ? t('ocr.stat.fps', (summary.fps ?? data?.fps)!) : null,
    summary.elapsed_sec != null ? `${t('ocr.stat.elapsed')} ${fmtSec(summary.elapsed_sec, 0)}` : null,
    summary.at ? t('ocr.stat.at', fmtRelative(summary.at)) : null,
  ].filter(Boolean) as string[]

  return (
    <Card id="ocr" className="scroll-mt-4 overflow-clip" data-shot="media-ocr">
      <CardHeader icon={<ScanText />} title={t('ocr.card.title')} description={t('ocr.card.desc')} actions={canRunOcr(m) ? <RunOcrButton m={m} videoAvailable={data?.video_available} /> : undefined} />
      <CardBody className="space-y-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-fg-3">
          <OcrStatusBadge status={status} active={active} />
          {meta.map((x) => (
            <span key={x} className="tabular">
              {x}
            </span>
          ))}
        </div>
        {status === 'failed' && (
          <div role="alert" className="flex gap-3 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
            <div className="min-w-0 text-[13px]">
              <div className="font-medium text-danger-fg">
                {t('ocr.empty.failed')}
                {summary.error_code && <code className="ml-2 font-mono text-xs opacity-80">{summary.error_code}</code>}
              </div>
              {summary.error && <p className="mt-0.5 leading-5 text-fg-2 break-cjk">{summary.error}</p>}
            </div>
          </div>
        )}
        {q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !data && q.isLoading ? (
          <Skeleton className="h-20" />
        ) : status === 'done' || events.length > 0 ? (
          <Summary s={summary} data={data} reason={reason} onReason={pickReason} />
        ) : null}
      </CardBody>

      {status === 'no_subtitles' && !events.length ? (
        <div className="border-t border-border">
          <EmptyState compact icon={<ScanText />} title={t('ocr.empty.noSubtitles')} description={t('ocr.empty.noSubtitlesDesc')} />
        </div>
      ) : (status === 'pending' || active) && !events.length ? (
        <div className="border-t border-border">
          <EmptyState compact icon={<Loader2 className="animate-spin" />} title={t('ocr.empty.pending')} />
        </div>
      ) : events.length > 0 || q.isLoading ? (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border px-4 py-2.5 sm:px-5">
            <Segmented
              aria-label={t('ocr.filter.label')}
              size="sm"
              value={reason ? 'rejected' : filter}
              onChange={(v) => {
                setFilter(v)
                setReason(null)
                setShown(STEP)
              }}
              options={[
                { value: 'all', label: t('ocr.filter.all'), count: counts.all },
                { value: 'paired', label: t('ocr.filter.paired'), count: counts.paired },
                { value: 'rejected', label: t('ocr.filter.rejected'), count: counts.rejected },
              ]}
            />
            {reason && (
              <button type="button" onClick={() => pickReason(null)} className="inline-flex h-7 items-center gap-1 rounded-full bg-warning-soft px-2.5 text-xs font-medium text-warning-fg hover:brightness-95">
                {ocrReason(reason)} ×
              </button>
            )}
            <span className="ml-auto text-xs tabular text-fg-3">{t('ocr.count', rows.length, list.length)}</span>
          </div>
          <div className="border-t border-border">
            {!data ? (
              <ListSkeleton rows={4} />
            ) : !rows.length ? (
              <EmptyState compact icon={<ScanText />} title={t('ocr.noEvents')} />
            ) : (
              <div className="divide-y divide-border">
                {rows.map((e) => (
                  <EventRow key={e.idx} e={e} mediaId={m.id} player={player} />
                ))}
              </div>
            )}
            {list.length > rows.length && (
              <div className="border-t border-border p-3 text-center">
                <Button variant="ghost" onClick={() => setShown((n) => n + STEP * 2)}>
                  <ChevronDown /> {t('ocr.showMore', list.length - rows.length, STEP * 2)}
                </Button>
              </div>
            )}
          </div>
        </>
      ) : null}
    </Card>
  )
}
