import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, AudioWaveform, Check, ChevronDown, ExternalLink, Loader2, Minus, RotateCw, X, ZoomIn, ZoomOut } from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useMediaDetail, useSegments, useTimeline } from '@/api/queries'
import type { Job, MediaDetail as TMedia, MediaQuality, SegmentSummary } from '@/api/types'
import { fmtClock, fmtCompact, fmtDate, fmtDateTime, fmtDb, fmtInt, fmtKHz, fmtMos, fmtPct, fmtTimestamp } from '@/lib/format'
import { flagLabel, jobKind, lang, licenseLabel, pipelineSteps } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, DescList, Skeleton, Tag, Thumb } from '@/components/ui/primitives'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger, Tooltip } from '@/components/ui/overlay'
import { DataTable, Pagination, type Column } from '@/components/ui/data-table'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { CaptionBadges, MediaStatusBadge, ReviewBadge } from '@/components/domain/status'
import { JobRow } from '@/components/domain/job-row'
import { BandwidthBar, SpectrumChart } from '@/components/domain/quality-viz'
import { LaneLabels, Lanes, lanesHeight, SpeakerLegend, type LaneKey } from '@/components/domain/lanes'
import { useSize } from '@/components/charts/charts'
import { speakerName } from '@/components/domain/speakers'

function Stepper({ m }: { m: TMedia }) {
  const byKind = new Map<string, Job>()
  ;[...m.jobs].reverse().forEach((j) => byKind.set(j.kind, j))
  const stageIdx = pipelineSteps.findIndex((s) => s.kind === m.stage)
  return (
    <ol className="flex min-w-0 items-start overflow-x-auto pb-1 no-scrollbar">
      {pipelineSteps.map((s, i) => {
        const j = byKind.get(s.kind)
        const st =
          m.status === 'ready' ? 'completed' : j?.status === 'completed' ? 'completed' : j?.status === 'failed' || (m.status === 'failed' && i === stageIdx) ? 'failed' : j?.status === 'running' ? 'running' : j?.status === 'queued' ? 'queued' : 'pending'
        return (
          <li key={s.kind} className="flex min-w-[38px] flex-1 flex-col items-center gap-1.5 sm:min-w-[64px]">
            <div className="flex w-full items-center">
              <span className={cn('h-px flex-1', i === 0 ? 'opacity-0' : st === 'completed' || st === 'running' ? 'bg-success' : 'bg-border-strong')} />
              <Tooltip content={`${jobKind[s.kind]}${j?.error_message ? `：${j.error_message}` : ''}`}>
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
              <span className={cn('h-px flex-1', i === pipelineSteps.length - 1 ? 'opacity-0' : st === 'completed' ? 'bg-success' : 'bg-border-strong')} />
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
    ['C50', fmtDb(q.c50_mean), '殘響'],
    ['語音比例', fmtPct(q.speech_ratio), `VAD 一致 ${fmtPct(q.vad_agreement)}`],
    ['響度', q.lufs_integrated != null ? `${q.lufs_integrated} LUFS` : '—', `峰值 ${fmtDb(q.true_peak_db)}`],
    ['削波', fmtPct(q.clipping_ratio, 2), q.clipping_ratio && q.clipping_ratio > 0.001 ? '偏高' : '正常'],
    ['位元率', q.bitrate ? `${Math.round(q.bitrate / 1000)} kbps` : '—', q.container ?? undefined],
  ]
  return (
    <Card>
      <CardHeader title="音質" description={`${q.codec ?? '—'} · ${fmtKHz(q.sample_rate)} · ${q.channels === 1 ? '單聲道' : '立體聲'}`} />
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
            <span className="text-[13px] font-medium">頻寬 vs. 奈奎斯特</span>
            {q.upsampled_suspect ? (
              <Badge tone="warning">
                <AlertTriangle className="size-3" /> 疑似升頻
              </Badge>
            ) : (
              <Badge tone="success" dot>
                頻寬正常
              </Badge>
            )}
          </div>
          <BandwidthBar bw={q.real_bandwidth_hz} sr={q.sample_rate} />
          {q.details?.spectrum && (
            <div className="mt-4">
              <SpectrumChart q={q} />
              <p className="mt-1 text-[11px] text-fg-3">原始音訊長期平均頻譜（dB）。陰影區為實際頻寬以上、取樣率可表示但沒有內容的頻段。</p>
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  )
}

function TimelineCard({ id, focus }: { id: string; focus?: number }) {
  const tl = useTimeline(id)
  const [ref, w] = useSize<HTMLDivElement>()
  const [zoom, setZoom] = useState(1)
  const navigate = useNavigate()
  const lanes: LaneKey[] = ['vad', 'snr', 'speaker', 'repeated', 'segments']
  return (
    <Card>
      <CardHeader
        title="時間軸"
        description="語音活動、SNR、語者分段、重複片段與音段；點選音段開始審查"
        actions={
          <div className="flex items-center gap-1">
            <Button size="icon-sm" variant="ghost" aria-label="縮小" disabled={zoom <= 1} onClick={() => setZoom((z) => Math.max(1, z / 2))}>
              <ZoomOut />
            </Button>
            <span className="w-8 text-center text-xs tabular text-fg-3">{zoom}×</span>
            <Button size="icon-sm" variant="ghost" aria-label="放大" disabled={zoom >= 16} onClick={() => setZoom((z) => Math.min(16, z * 2))}>
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
                    onSegmentClick={(sid) => navigate(`/review?media=${id}&segment=${sid}`)}
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

function SegmentsCard({ id }: { id: string }) {
  const [offset, setOffset] = useState(0)
  const segs = useSegments({ media_id: id, limit: 25, offset })
  const navigate = useNavigate()
  const cols: Column<SegmentSummary>[] = [
    { id: 't', header: '時間', width: 'w-28', cell: (s) => <span className="font-mono text-xs text-fg-2">{fmtTimestamp(s.start_sec)}</span> },
    { id: 'text', header: '文字', cell: (s) => <span className="line-clamp-2 break-cjk text-[13px]">{s.text}</span> },
    { id: 'sp', header: '語者', width: 'w-28', hideBelow: 'xl', cell: (s) => <span className="truncate text-xs text-fg-2">{s.speaker ? speakerName(s.speaker) : s.local_speaker ?? '—'}</span> },
    { id: 'snr', header: 'SNR', width: 'w-20', align: 'right', hideBelow: 'lg', cell: (s) => <span className="text-xs">{fmtDb(s.snr)}</span> },
    { id: 'st', header: '審查', width: 'w-24', cell: (s) => <ReviewBadge status={s.review_status} /> },
  ]
  return (
    <Card className="overflow-clip">
      <CardHeader
        title="音段"
        description={segs.data ? `${fmtInt(segs.data.total)} 個音段` : undefined}
        actions={
          <Button asChild size="sm" variant="soft">
            <Link to={`/review?media=${id}`}>
              <AudioWaveform /> 審查這部影片
            </Link>
          </Button>
        }
      />
      <div className="mt-3 border-t border-border">
        {!segs.data ? (
          <ListSkeleton rows={5} />
        ) : !segs.data.items.length ? (
          <EmptyState compact icon={<AudioWaveform />} title="尚未切分音段" description="影片處理完成後會依字幕與 VAD 自動切分。" />
        ) : (
          <>
            <DataTable
              rows={segs.data.items}
              columns={cols}
              dense
              onRowClick={(s) => navigate(`/review?media=${id}&segment=${s.id}`)}
              card={(s) => (
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-xs text-fg-3">
                      {fmtTimestamp(s.start_sec)} · {s.duration.toFixed(1)} 秒
                    </span>
                    <ReviewBadge status={s.review_status} />
                  </div>
                  <p className="mt-1 text-[13px] leading-5 break-cjk">{s.text}</p>
                  {s.flags.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {s.flags.map((f) => (
                        <Tag key={f}>{flagLabel[f] ?? f}</Tag>
                      ))}
                    </div>
                  )}
                </div>
              )}
            />
            <Pagination total={segs.data.total} limit={25} offset={offset} onChange={setOffset} className="border-t border-border" />
          </>
        )}
      </div>
    </Card>
  )
}

export default function MediaDetail() {
  const { id = '' } = useParams()
  const md = useMediaDetail(id)
  const m = md.data
  useTitle(m?.title)
  const qc = useQueryClient()
  const [descOpen, setDescOpen] = useState(false)
  const retry = useMutation({
    mutationFn: () => api.retryMedia(id),
    onSuccess: () => {
      toast.success('已重新排入處理')
      qc.invalidateQueries({ queryKey: qk.mediaDetail(id) })
    },
    onError: onErr,
  })
  const reprocess = useMutation({
    mutationFn: (step: string) => api.reprocess(id, step),
    onSuccess: () => {
      toast.success('已排入重新處理')
      qc.invalidateQueries({ queryKey: qk.mediaDetail(id) })
    },
    onError: onErr,
  })
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

  const hasPipeline = !['listed', 'detailed'].includes(m.status)
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
            <MediaStatusBadge status={m.status} stage={m.stage} />
            <span>{fmtDate(m.published_at)}</span>
            <span>{fmtCompact(m.view_count)} 次觀看</span>
            <span>{licenseLabel[m.license ?? 'youtube']}</span>
            {m.kind === 'short' && <Tag>Shorts</Tag>}
            <CaptionBadges manual={m.has_manual_captions} auto={m.has_auto_captions} manualLangs={m.manual_caption_langs} autoLangs={m.auto_caption_langs} />
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {m.status === 'ready' && (
              <Button asChild variant="primary">
                <Link to={`/review?media=${m.id}`}>
                  <AudioWaveform /> 審查音段
                </Link>
              </Button>
            )}
            {m.status === 'failed' && (
              <Button variant="primary" onClick={() => retry.mutate()} loading={retry.isPending}>
                <RotateCw /> 重試失敗的步驟
              </Button>
            )}
            {hasPipeline && (
              <Menu>
                <MenuTrigger asChild>
                  <Button>
                    重新處理 <ChevronDown />
                  </Button>
                </MenuTrigger>
                <MenuContent align="start">
                  <MenuLabel>從這一步重新執行</MenuLabel>
                  {pipelineSteps.slice(1).map((s) => (
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
              處理失敗{m.stage ? `：${jobKind[m.stage as keyof typeof jobKind] ?? m.stage}` : ''}
              {m.failure_code && <code className="ml-2 font-mono text-xs opacity-80">{m.failure_code}</code>}
            </div>
            <p className="mt-0.5 leading-5 text-fg-2 break-cjk">{m.failure_message ?? '沒有提供錯誤訊息。'}</p>
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
              <TimelineCard id={m.id} focus={focus} />
              <SegmentsCard id={m.id} />
            </>
          ) : (
            <Card>
              <EmptyState
                icon={hasPipeline ? <Loader2 className="animate-spin" /> : <Minus />}
                title={hasPipeline ? (m.status === 'failed' ? '處理在中途停止' : '處理中，完成後會顯示時間軸與音段') : '這部影片尚未下載'}
                description={hasPipeline ? '可在下方的工作紀錄查看每一步的狀態。' : '回到頻道頁勾選後批次下載，或確認它有原生字幕。'}
              />
            </Card>
          )}
          <Card>
            <CardHeader title="工作紀錄" description={`${m.jobs.length} 筆`} />
            <div className="mt-3 divide-y divide-border border-t border-border">
              {m.jobs.length ? m.jobs.map((j) => <JobRow key={j.id} job={j} hideSubject />) : <p className="px-5 py-6 text-center text-[13px] text-fg-3">沒有工作紀錄</p>}
            </div>
          </Card>
        </div>
        <div className="min-w-0 space-y-4">
          {m.quality && <QualityCard q={m.quality} />}
          <Card>
            <CardHeader title="影片資訊" />
            <CardBody className="space-y-4">
              <DescList
                items={[
                  ['YouTube ID', <code className="font-mono text-xs">{m.platform_media_id}</code>],
                  ['長度', fmtClock(m.duration_sec)],
                  ['發布', fmtDateTime(m.published_at)],
                  ['讚 / 留言', `${fmtInt(m.like_count)} / ${fmtInt(m.comment_count)}`],
                  ['語言', m.language ? lang(m.language) : '—'],
                  ['字幕軌', m.tracks.length ? m.tracks.map((t) => `${lang(t.language)}（${t.kind === 'manual' ? '原生' : t.kind === 'auto' ? '自動' : t.kind}）`).join('、') : '—'],
                  ['章節', m.chapters.length ? m.chapters.map((c) => c.title).join('、') : '—'],
                ]}
              />
              {m.description && (
                <div className="border-t border-border pt-3">
                  <p className={cn('whitespace-pre-line text-[13px] leading-6 text-fg-2 break-cjk', !descOpen && 'line-clamp-4')}>{m.description}</p>
                  <button onClick={() => setDescOpen((o) => !o)} className="mt-1 text-xs font-medium text-primary hover:underline">
                    {descOpen ? '收合' : '顯示完整說明'}
                  </button>
                </div>
              )}
              {m.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {m.tags.map((t) => (
                    <Tag key={t}>#{t}</Tag>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  )
}
