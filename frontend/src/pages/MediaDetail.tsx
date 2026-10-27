import { t } from '@/i18n'
import { useMemo, useState } from 'react'
import { DownloadDialog } from '@/components/domain/media-browser'
import { Link, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, AudioWaveform, Check, ChevronDown, ExternalLink, Languages, Loader2, Minus, RotateCw, X, ZoomIn, ZoomOut, Download } from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useMediaDetail, useTimeline } from '@/api/queries'
import type { Job, MediaDetail as TMedia, MediaQuality, Track } from '@/api/types'
import { fmtClock, fmtCompact, fmtDate, fmtDateTime, fmtDb, fmtInt, fmtKHz, fmtMos, fmtPct } from '@/lib/format'
import { jobKind, lang, licenseLabel, pipelineSteps, trackScript } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, DescList, Skeleton, Tag, Thumb } from '@/components/ui/primitives'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { CaptionBadges, MediaStatusBadge } from '@/components/domain/status'
import { JobRow } from '@/components/domain/job-row'
import { BandwidthBar, SpectrumChart } from '@/components/domain/quality-viz'
import { LaneLabels, Lanes, lanesHeight, SpeakerLegend, type LaneKey } from '@/components/domain/lanes'
import { useSize } from '@/components/charts/charts'
import { useSentencePlayer } from '@/hooks/useSentencePlayer'
import { TranscriptCard } from './media/Transcript'

function Stepper({ m }: { m: TMedia }) {
  const byKind = new Map<string, Job>()
  ;[...m.jobs].reverse().forEach((j) => byKind.set(j.kind, j))
  const stageIdx = pipelineSteps().findIndex((s) => s.kind === m.stage)
  return (
    <ol className="flex min-w-0 items-start overflow-x-auto pb-1 no-scrollbar">
      {pipelineSteps().map((s, i) => {
        const j = byKind.get(s.kind)
        const st =
          m.status === 'ready' ? 'completed' : j?.status === 'completed' ? 'completed' : j?.status === 'failed' || (m.status === 'failed' && i === stageIdx) ? 'failed' : j?.status === 'running' ? 'running' : j?.status === 'queued' ? 'queued' : 'pending'
        return (
          <li key={s.kind} className="flex min-w-[38px] flex-1 flex-col items-center gap-1.5 sm:min-w-[64px]">
            <div className="flex w-full items-center">
              <span className={cn('h-px flex-1', i === 0 ? 'opacity-0' : st === 'completed' || st === 'running' ? 'bg-success' : 'bg-border-strong')} />
              <Tooltip content={t('process.media.stepTip', jobKind[s.kind], j?.error_message ?? '')}>
                <span
                  className={cn(
                    'grid size-6 shrink-0 place-items-center rounded-full border-2 text-[11px] font-semibold sm:size-7',
                    st === 'completed' && 'border-success bg-success text-white',
                    st === 'running' && 'border-primary bg-primary-soft text-primary',
                    st === 'failed' && 'border-danger bg-danger text-white',
                    st === 'queued' && 'border-border-strong bg-surface text-fg-3',
                    st === 'pending' && 'border-border bg-surface text-fg-3',
                  )}
                >
                  {st === 'completed' ? <Check className="size-3.5" strokeWidth={3} /> : st === 'running' ? <Loader2 className="size-3.5 animate-spin" /> : st === 'failed' ? <X className="size-3.5" strokeWidth={3} /> : i + 1}
                </span>
              </Tooltip>
              <span className={cn('h-px flex-1', i === pipelineSteps().length - 1 ? 'opacity-0' : st === 'completed' ? 'bg-success' : 'bg-border-strong')} />
            </div>
            <span className={cn('whitespace-nowrap text-[11px]', st === 'running' ? 'font-medium text-primary' : st === 'failed' ? 'font-medium text-danger-fg' : 'text-fg-3')}>{s.short}</span>
          </li>
        )
      })}
    </ol>
  )
}

function QualityCard({ q }: { q: MediaQuality }) {
  const metrics: [string, string, string?][] = [
    ['SNR', fmtDb(q.snr_mean), `P10 ${fmtDb(q.snr_p10)}`],
    ['DNSMOS OVRL', fmtMos(q.dnsmos_ovrl), `SIG ${fmtMos(q.dnsmos_sig)} · BAK ${fmtMos(q.dnsmos_bak)}`],
    ['P.808', fmtMos(q.dnsmos_p808)],
    ['C50', fmtDb(q.c50_mean), t('process.media.reverb')],
    [t('process.media.speechRatio'), fmtPct(q.speech_ratio), t('process.media.vadAgreement', fmtPct(q.vad_agreement))],
    [t('process.media.loudness'), q.lufs_integrated != null ? `${q.lufs_integrated} LUFS` : '—', t('process.media.peak', fmtDb(q.true_peak_db))],
    [t('process.media.clipping'), fmtPct(q.clipping_ratio, 2), q.clipping_ratio && q.clipping_ratio > 0.001 ? t('process.media.high') : t('process.media.normal')],
    [t('process.media.bitrate'), q.bitrate ? `${Math.round(q.bitrate / 1000)} kbps` : '—', q.container ?? undefined],
  ]
  return (
    <Card>
      <CardHeader title={t('process.media.quality')} description={`${q.codec ?? '—'} · ${fmtKHz(q.sample_rate)} · ${q.channels === 1 ? t('process.media.mono') : t('process.media.stereo')}`} />
      <CardBody className="space-y-5">
        <div className="grid grid-cols-2 gap-2">
          {metrics.map(([k, v, h]) => (
            <div key={k} className="min-w-0 rounded-lg bg-surface-2/70 px-3 py-2">
              <div className="truncate text-[11px] text-fg-3">{k}</div>
              <div className="text-[15px] font-semibold leading-6 tabular">{v}</div>
              {h && <div className="truncate text-[11px] text-fg-3">{h}</div>}
            </div>
          ))}
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-[13px] font-medium">{t('process.media.bwVsNyquist')}</span>
            {q.upsampled_suspect ? (
              <Badge tone="warning">
                <AlertTriangle className="size-3" /> {t('process.media.upsampled')}
              </Badge>
            ) : (
              <Badge tone="success" dot>
                {t('process.media.bwOk')}
              </Badge>
            )}
          </div>
          <BandwidthBar bw={q.real_bandwidth_hz} sr={q.sample_rate} />
          {q.details?.spectrum && (
            <div className="mt-4">
              <SpectrumChart q={q} />
              <p className="mt-1 text-[11px] text-fg-3">{t('process.media.spectrumNote')}</p>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  )
}

function TimelineCard({ id, focus, onSegment }: { id: string; focus?: number; onSegment: (s: { id: string; start_sec: number; revision: number }) => void }) {
  const tl = useTimeline(id)
  const [ref, w] = useSize<HTMLDivElement>()
  const [zoom, setZoom] = useState(1)
  const lanes: LaneKey[] = ['vad', 'snr', 'speaker', 'repeated', 'segments']
  return (
    <Card>
      <CardHeader
        title={t('process.media.timeline')}
        description={t('process.media.timelineDesc')}
        actions={
          <div className="flex items-center gap-1">
            <Button size="icon-sm" variant="ghost" aria-label={t('process.media.zoomOut')} disabled={zoom <= 1} onClick={() => setZoom((z) => Math.max(1, z / 2))}>
              <ZoomOut />
            </Button>
            <span className="w-8 text-center text-xs tabular text-fg-3">{zoom}×</span>
            <Button size="icon-sm" variant="ghost" aria-label={t('process.media.zoomIn')} disabled={zoom >= 16} onClick={() => setZoom((z) => Math.min(16, z * 2))}>
              <ZoomIn />
            </Button>
          </div>
        }
      />
      <CardBody>
        {tl.isError ? (
          <ErrorState error={tl.error} />
        ) : !tl.data ? (
          <Skeleton className="h-36" />
        ) : (
          <>
            <div className="flex gap-3">
              <LaneLabels lanes={lanes} className="w-14" />
              <div ref={ref} className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden scrollbar-thin" style={{ height: lanesHeight(lanes) + 8 }}>
                {w > 0 && (
                  <Lanes
                    t={tl.data}
                    domain={[0, tl.data.duration ?? 0]}
                    width={w * zoom}
                    lanes={lanes}
                    playhead={focus ?? null}
                    onSegmentClick={(sid) => {
                      const s = tl.data!.segments.find((x) => x.id === sid)
                      if (s) onSegment(s)
                    }}
                  />
                )}
              </div>
            </div>
            <div className="mt-3 border-t border-border pt-3">
              <SpeakerLegend t={tl.data} />
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

/** Caption tracks with their Simplified → Traditional conversion state. */
function TrackList({ tracks }: { tracks: Track[] }) {
  if (!tracks.length) return <>—</>
  return (
    <ul className="space-y-1">
      {tracks.map((tr) => (
        <li key={tr.id} className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
          <span>{t('process.media.track', lang(tr.language), tr.kind === 'manual' ? t('process.media.trackKind.manual') : tr.kind === 'auto' ? t('process.media.trackKind.auto') : tr.kind)}</span>
          {tr.s2t_converted ? (
            <Tag className="border-info/30 bg-info-soft text-info-fg">
              <Languages className="size-3" />
              {t('process.media.trackConverted', tr.cues_converted ?? 0)}
            </Tag>
          ) : tr.script && tr.script !== 'not_chinese' && tr.script !== 'traditional' ? (
            <Tag>{trackScript[tr.script] ?? tr.script}</Tag>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

export default function MediaDetail() {
  const { id = '' } = useParams()
  const md = useMediaDetail(id)
  const m = md.data
  useTitle(m?.title)
  const qc = useQueryClient()
  const [descOpen, setDescOpen] = useState(false)
  const [dlOpen, setDlOpen] = useState(false)
  const retry = useMutation({
    mutationFn: () => api.retryMedia(id),
    onSuccess: () => {
      toast.success(t('process.media.requeued'))
      qc.invalidateQueries({ queryKey: qk.mediaDetail(id) })
    },
    onError: onErr,
  })
  const reprocess = useMutation({
    mutationFn: (step: string) => api.reprocess(id, step),
    onSuccess: () => {
      toast.success(t('process.media.reprocessQueued'))
      qc.invalidateQueries({ queryKey: qk.mediaDetail(id) })
    },
    onError: onErr,
  })
  const convert = useMutation({
    mutationFn: () => api.convertMediaText(id),
    onSuccess: (r) => {
      toast.success(r.queued ? t('process.media.s2tQueued') : t('process.media.s2tNothing'))
      qc.invalidateQueries({ queryKey: qk.mediaDetail(id) })
    },
    onError: onErr,
  })
  const player = useSentencePlayer()
  const focus = useMemo(() => Number(new URLSearchParams(location.search).get('t')) || undefined, [])

  if (md.isError)
    return (
      <Page>
        <Card>
          <ErrorState error={md.error} onRetry={() => md.refetch()} />
        </Card>
      </Page>
    )
  if (!m)
    return (
      <Page>
        <div className="flex gap-4">
          <Skeleton className="h-24 w-40 rounded-lg" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-7 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
          </div>
        </div>
        <Skeleton className="mt-6 h-48 rounded-xl" />
      </Page>
    )

  const hasPipeline = !['listed', 'detailed', 'skipped', 'blocked'].includes(m.status)
  const membersOnly = m.failure_code === 'members_only'
  const canDownload = !membersOnly && ['listed', 'detailed', 'skipped', 'blocked', 'failed'].includes(m.status) && !m.jobs.some((j) => j.kind !== 'media.download' && ['queued', 'running'].includes(j.status)) && m.segment_count === 0
  return (
    <Page wide>
      {m.channel && (
        <Link to={`/channels/${m.channel.id}`} className="-ml-1 mb-3 inline-flex h-8 max-w-full items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 hover:bg-surface-2 hover:text-fg">
          <span className="truncate">‹ {m.channel.title}</span>
        </Link>
      )}
      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <Thumb src={m.thumbnail_url} seed={m.id} className={cn('rounded-xl', m.kind === 'short' ? 'aspect-[9/16] w-28 md:w-36' : 'aspect-video w-44 md:w-56')}>
          <span className="absolute bottom-1.5 right-1.5 rounded bg-black/70 px-1.5 text-xs font-medium leading-5 text-white tabular">{fmtClock(m.duration_sec)}</span>
        </Thumb>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold leading-7 tracking-tight break-cjk sm:text-[22px] sm:leading-8">{m.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-[13px] text-fg-3">
            <MediaStatusBadge status={m.status} stage={m.stage} failureCode={m.failure_code} />
            <span>{fmtDate(m.published_at)}</span>
            <span>{t('process.media.views', fmtCompact(m.view_count))}</span>
            <span>{licenseLabel[m.license ?? 'youtube']}</span>
            {m.kind === 'short' && <Tag>Shorts</Tag>}
            <CaptionBadges manual={m.has_manual_captions} auto={m.has_auto_captions} manualLangs={m.manual_caption_langs} autoLangs={m.auto_caption_langs} />
            {m.text_converted && (
              <Tooltip content={t('process.media.s2tTip', m.text_conversion?.cues_converted ?? m.tracks.reduce((a, x) => a + (x.cues_converted ?? 0), 0))}>
                <Badge tone="info">
                  <Languages className="size-3" /> {t('process.media.s2tBadge')}
                </Badge>
              </Tooltip>
            )}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {canDownload && (
              <Button variant="primary" onClick={() => setDlOpen(true)}>
                <Download /> {t('process.media.download')}
              </Button>
            )}
            {m.status === 'ready' && (
              <Button asChild variant="primary">
                <Link to={`/review?media=${m.id}`}>
                  <AudioWaveform /> {t('process.media.reviewSegments')}
                </Link>
              </Button>
            )}
            {m.status === 'failed' && (
              <Button variant="primary" onClick={() => retry.mutate()} loading={retry.isPending}>
                <RotateCw /> {t('process.media.retryFailed')}
              </Button>
            )}
            {m.text_convertible && (
              <Button onClick={() => convert.mutate()} loading={convert.isPending || m.jobs.some((j) => j.kind === 'media.convert_text' && (j.status === 'queued' || j.status === 'running'))}>
                <Languages /> {t('process.media.s2tConvert')}
              </Button>
            )}
            {hasPipeline && (
              <Menu>
                <MenuTrigger asChild>
                  <Button>
                    {t('process.media.reprocess')} <ChevronDown />
                  </Button>
                </MenuTrigger>
                <MenuContent align="start">
                  <MenuLabel>{t('process.media.rerunFrom')}</MenuLabel>
                  {pipelineSteps().slice(1).map((s) => (
                    <MenuItem key={s.kind} onSelect={() => reprocess.mutate(s.kind)}>
                      {jobKind[s.kind]}
                    </MenuItem>
                  ))}
                </MenuContent>
              </Menu>
            )}
            <Button asChild variant="ghost">
              <a href={m.url} target="_blank" rel="noreferrer noopener">
                YouTube <ExternalLink />
              </a>
            </Button>
          </div>
        </div>
      </div>

      {m.status === 'failed' && (
        <div role="alert" className="mt-5 flex gap-3 rounded-xl border border-danger/25 bg-danger-soft px-4 py-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
          <div className="min-w-0 text-[13px]">
            <div className="font-medium text-danger-fg">
              {m.stage ? t('process.media.failedAt', jobKind[m.stage as keyof typeof jobKind] ?? m.stage) : t('process.media.failed')}
              {m.failure_code && <code className="ml-2 font-mono text-xs opacity-80">{m.failure_code}</code>}
            </div>
            <p className="mt-0.5 leading-5 text-fg-2 break-cjk">{m.failure_message ?? t('process.media.noErrorMessage')}</p>
          </div>
        </div>
      )}

      {hasPipeline && (
        <Card className="mt-5 px-3 py-4 sm:px-5">
          <Stepper m={m} />
        </Card>
      )}

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          {m.status === 'ready' || m.segment_count > 0 ? (
            <>
              <TimelineCard id={m.id} focus={focus} onSegment={player.play} />
              <TranscriptCard mediaId={m.id} player={player} focusId={player.id} />
            </>
          ) : (
            <Card>
              <EmptyState
                icon={hasPipeline ? <Loader2 className="animate-spin" /> : <Minus />}
                title={hasPipeline ? (m.status === 'failed' ? t('process.media.stoppedMidway') : t('process.media.processing')) : t('process.media.notDownloaded')}
                description={hasPipeline ? t('process.media.processingDesc') : membersOnly ? t('process.media.notDownloadableDesc') : t('process.media.notDownloadedDesc')}
                action={
                  canDownload && !hasPipeline ? (
                    <Button variant="primary" onClick={() => setDlOpen(true)}>
                      <Download /> {t('process.media.download')}
                    </Button>
                  ) : undefined
                }
              />
            </Card>
          )}
          <Card>
            <CardHeader title={t('process.media.jobHistory')} description={t('process.media.jobCount', m.jobs.length)} />
            <div className="mt-3 divide-y divide-border border-t border-border">
              {m.jobs.length ? m.jobs.map((j) => <JobRow key={j.id} job={j} hideSubject />) : <p className="px-5 py-6 text-center text-[13px] text-fg-3">{t('process.media.noJobs')}</p>}
            </div>
          </Card>
        </div>
        <div className="min-w-0 space-y-4">
          {m.quality && <QualityCard q={m.quality} />}
          <Card>
            <CardHeader title={t('process.media.info')} />
            <CardBody className="space-y-4">
              <DescList
                items={[
                  ['YouTube ID', <code className="font-mono text-xs">{m.platform_media_id}</code>],
                  [t('process.media.duration'), fmtClock(m.duration_sec)],
                  [t('process.media.published'), fmtDateTime(m.published_at)],
                  [t('process.media.likesComments'), `${fmtInt(m.like_count)} / ${fmtInt(m.comment_count)}`],
                  [t('process.media.language'), m.language ? lang(m.language) : '—'],
                  [t('process.media.tracks'), <TrackList tracks={m.tracks} />],
                  [t('process.media.chapters'), m.chapters.length ? m.chapters.map((c) => c.title).join(t('common.listSep')) : '—'],
                ]}
              />
              {m.description && (
                <div className="border-t border-border pt-3">
                  <p className={cn('whitespace-pre-line text-[13px] leading-6 text-fg-2 break-cjk', !descOpen && 'line-clamp-4')}>{m.description}</p>
                  <button onClick={() => setDescOpen((o) => !o)} className="mt-1 text-xs font-medium text-primary hover:underline">
                    {descOpen ? t('process.media.collapse') : t('process.media.showDescription')}
                  </button>
                </div>
              )}
              {m.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {m.tags.map((tag) => (
                    <Tag key={tag}>#{tag}</Tag>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
      <DownloadDialog
        open={dlOpen}
        onOpenChange={setDlOpen}
        count={1}
        selection={{ ids: [m.id] }}
        query={{ include_auto: !m.has_manual_captions && !!m.has_auto_captions }}
        sample={[m]}
        onDone={() => undefined}
      />
    </Page>
  )
}
