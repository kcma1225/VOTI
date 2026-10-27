import { t } from '@/i18n'
import { Link } from 'react-router'
import { AlertTriangle, Gauge } from 'lucide-react'
import { useMedia, useQualitySummary, useSettings } from '@/api/queries'
import type { HistBin, MediaQuality } from '@/api/types'
import { fmt1, fmtInt, fmtKHz, fmtPct } from '@/lib/format'
import { cn } from '@/lib/cn'
import { Card, CardHeader, Skeleton, Stat } from '@/components/ui/primitives'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { Tooltip } from '@/components/ui/overlay'
import { ChartCard, HBars, Histogram, SERIES, useSize } from '@/components/charts/charts'

/** Real bandwidth vs Nyquist (sample_rate / 2). A gap means the file was likely upsampled. */
export function BandwidthBar({ bw, sr, compact, className }: { bw: number | null; sr: number | null; compact?: boolean; className?: string }) {
  if (!bw || !sr) return <span className="text-xs text-fg-3">—</span>
  const nyq = sr / 2
  const ratio = Math.min(1, bw / nyq)
  const suspect = ratio < 0.6
  return (
    <div className={cn('min-w-0', className)}>
      <div className="relative h-2 w-full rounded-full bg-surface-3">
        <div className={cn('absolute inset-y-0 left-0 rounded-full', suspect ? 'bg-warning' : 'bg-series-1')} style={{ width: `${ratio * 100}%` }} />
        <span className="absolute -top-0.5 right-0 h-3 w-px bg-fg-3" />
      </div>
      {!compact && (
        <div className="mt-1 flex justify-between text-[11px] tabular text-fg-3">
          <span>{t('process.qualityViz.actual', fmtKHz(bw))}</span>
          <span>{t('process.qualityViz.nyquist', fmtKHz(nyq))}</span>
        </div>
      )}
    </div>
  )
}

/** Long-term spectrum of the original audio, with the measured bandwidth cut-off and Nyquist marked. */
export function SpectrumChart({ q, height = 170 }: { q: MediaQuality; height?: number }) {
  const [ref, w] = useSize<HTMLDivElement>()
  const spec = q.details?.spectrum
  const sr = q.sample_rate ?? 48000
  const nyq = sr / 2
  const bw = q.real_bandwidth_hz ?? nyq
  const padL = 34
  const padB = 22
  const plotW = Math.max(0, w - padL - 4)
  const plotH = height - padB
  const X = (hz: number) => padL + (hz / nyq) * plotW
  const minDb = -100
  const maxDb = -10
  const Y = (db: number) => ((maxDb - Math.max(minDb, Math.min(maxDb, db))) / (maxDb - minDb)) * plotH
  const path = spec?.map((p, i) => `${i ? 'L' : 'M'}${X(p.hz).toFixed(1)},${Y(p.db).toFixed(1)}`).join(' ')
  const ticks = [0, nyq / 4, nyq / 2, (3 * nyq) / 4, nyq]
  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {w > 0 && (
        <svg width={w} height={height} className="block" role="img" aria-label={t('process.qualityViz.spectrum')}>
          {[-20, -50, -80].map((d) => (
            <g key={d}>
              <line x1={padL} x2={w} y1={Y(d)} y2={Y(d)} stroke="var(--chart-grid)" />
              <text x={padL - 5} y={Y(d)} dy="0.32em" textAnchor="end" className="fill-fg-3 text-[10px]">
                {d}
              </text>
            </g>
          ))}
          <rect x={X(bw)} y={0} width={Math.max(0, X(nyq) - X(bw))} height={plotH} fill={bw / nyq < 0.6 ? 'var(--warning)' : 'var(--fg-3)'} opacity={0.1} />
          {path && (
            <>
              <path d={`${path} L${X(nyq)},${plotH} L${X(0)},${plotH} Z`} fill="var(--series-1)" opacity={0.1} />
              <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />
            </>
          )}
          <line x1={X(bw)} x2={X(bw)} y1={0} y2={plotH} stroke="var(--fg-2)" strokeWidth={1.5} />
          <text x={X(bw) - 4} y={11} textAnchor="end" className="fill-fg-2 text-[10px] font-medium">
            {t('process.qualityViz.actualBandwidth', fmtKHz(bw))}
          </text>
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--chart-axis)" />
          {ticks.map((tk) => (
            <text key={tk} x={X(tk)} y={height - 6} textAnchor={tk === 0 ? 'start' : tk === nyq ? 'end' : 'middle'} className="fill-fg-3 text-[10px] tabular">
              {fmt1(tk / 1000)}k
            </text>
          ))}
        </svg>
      )}
    </div>
  )
}

const hz = (b: HistBin[]) => b.map((x) => ({ ...x }))

export function QualityPanels({ channelId }: { channelId?: string }) {
  const q = useQualitySummary(channelId)
  const settings = useSettings()
  const suspects = useMedia({ channel_id: channelId, upsampled_suspect: true, status: ['ready'], limit: 8, sort: '-published_at', include_auto: true })
  const snrTh = settings.data?.quality_thresholds.low_snr_db ?? 10
  const srTh = settings.data?.quality_thresholds.low_speech_ratio ?? 0.5
  if (q.isError) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>
  if (!q.data)
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-64 rounded-xl" />
        ))}
      </div>
    )
  const d = q.data
  const n = d.media_count ?? Object.values(d.sample_rates).reduce((a, b) => a + b, 0)
  if (!n)
    return (
      <Card>
        <EmptyState icon={<Gauge />} title={t('process.qualityViz.emptyTitle')} description={t('process.qualityViz.emptyDesc')} />
      </Card>
    )
  const srs = Object.entries(d.sample_rates).sort((a, b) => b[1] - a[1])
  const codecs = Object.entries(d.codecs).sort((a, b) => b[1] - a[1])
  const tableOf = (bins: HistBin[], label: (b: number) => string) => ({ columns: [t('process.qualityViz.colRange'), t('process.qualityViz.colVideos')], rows: bins.map((b) => [label(b.bin), b.count]) })
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="p-4">
          <Stat label={t('process.qualityViz.analyzed')} value={fmtInt(n)} unit={t('process.qualityViz.videosUnit')} />
        </Card>
        <Card className="p-4">
          <Stat
            label={t('process.qualityViz.upsampled')}
            value={fmtInt(d.upsampled_suspect)}
            unit={t('process.qualityViz.videosUnit')}
            hint={t('process.qualityViz.upsampledHint', fmtPct(d.upsampled_suspect / n, 1))}
            icon={d.upsampled_suspect ? <AlertTriangle className="text-warning" /> : undefined}
          />
        </Card>
        <Card className="p-4">
          <Stat label={t('process.qualityViz.mainSampleRate')} value={srs[0] ? fmt1(Number(srs[0][0]) / 1000) : '—'} unit="kHz" hint={srs[0] ? t('process.qualityViz.ofVideos', fmtPct(srs[0][1] / n)) : undefined} />
        </Card>
        <Card className="p-4">
          <Stat label={t('process.qualityViz.mainCodec')} value={codecs[0]?.[0] ?? '—'} hint={codecs[0] ? t('process.qualityViz.ofVideos', fmtPct(codecs[0][1] / n)) : undefined} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title={t('process.qualityViz.snrTitle')} description={t('process.qualityViz.snrDesc', snrTh)} table={tableOf(d.histograms.snr, (b) => `${b} dB`)}>
          <Histogram bins={hz(d.histograms.snr)} binLabel={(b) => `${b}`} threshold={{ value: snrTh, label: `${snrTh} dB`, binWidth: 2.5, start: 0 }} highlight={(b) => b >= snrTh - 1e-9} />
        </ChartCard>
        <ChartCard title={t('process.qualityViz.dnsmosTitle')} description={t('process.qualityViz.dnsmosDesc')} table={tableOf(d.histograms.dnsmos_ovrl, (b) => b.toFixed(1))}>
          <Histogram bins={hz(d.histograms.dnsmos_ovrl)} binLabel={(b) => b.toFixed(1)} />
        </ChartCard>
        <ChartCard title={t('process.qualityViz.bandwidthTitle')} description={t('process.qualityViz.bandwidthDesc')} table={tableOf(d.histograms.bandwidth_hz, (b) => `${b / 1000} kHz`)}>
          <Histogram bins={hz(d.histograms.bandwidth_hz)} binLabel={(b) => `${b / 1000}k`} highlight={(b) => b >= 7000} />
        </ChartCard>
        <ChartCard title={t('process.qualityViz.speechTitle')} description={t('process.qualityViz.speechDesc', fmtPct(srTh))} table={tableOf(d.histograms.speech_ratio, (b) => fmtPct(b))}>
          <Histogram bins={hz(d.histograms.speech_ratio)} binLabel={(b) => `${Math.round(b * 100)}%`} threshold={{ value: srTh, label: fmtPct(srTh), binWidth: 0.05, start: 0 }} highlight={(b) => b >= srTh - 1e-9} />
        </ChartCard>
        <ChartCard title={t('process.qualityViz.c50Title')} description={t('process.qualityViz.c50Desc')} table={tableOf(d.histograms.c50, (b) => `${b} dB`)}>
          <Histogram bins={hz(d.histograms.c50)} binLabel={(b) => `${b}`} />
        </ChartCard>
        <Card className="flex flex-col">
          <CardHeader title={t('process.qualityViz.srCodecTitle')} description={t('process.qualityViz.srCodecDesc')} />
          <div className="grid flex-1 gap-6 px-4 pb-5 pt-4 sm:grid-cols-2 sm:px-5">
            <div className="min-w-0">
              <div className="mb-3 text-xs font-medium text-fg-3">{t('process.qualityViz.sampleRate')}</div>
              <HBars rows={srs.map(([k, v], i) => ({ key: k, label: `${fmt1(Number(k) / 1000)} kHz`, value: v, color: SERIES[i] }))} format={(v) => t('process.qualityViz.videos', v)} />
            </div>
            <div className="min-w-0">
              <div className="mb-3 text-xs font-medium text-fg-3">{t('process.qualityViz.codec')}</div>
              <HBars rows={codecs.map(([k, v], i) => ({ key: k, label: k, value: v, color: SERIES[i] }))} format={(v) => t('process.qualityViz.videos', v)} />
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title={t('process.qualityViz.suspectsTitle')}
          description={t('process.qualityViz.suspectsDesc')}
          icon={<AlertTriangle className="text-warning" />}
        />
        <div className="mt-3 border-t border-border">
          {suspects.data?.items.length ? (
            <ul className="divide-y divide-border">
              {suspects.data.items.map((m) => (
                <li key={m.id}>
                  <Link to={`/media/${m.id}`} className="grid grid-cols-1 items-center gap-2 px-4 py-3 hover:bg-surface-2/60 sm:grid-cols-[minmax(0,1fr)_minmax(0,16rem)] sm:gap-6 sm:px-5">
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium">{m.title}</div>
                      <div className="truncate text-xs text-fg-3">{m.channel?.title}</div>
                    </div>
                    <Tooltip content={t('process.qualityViz.barTip')}>
                      <div>
                        <BandwidthBar bw={m.quality?.real_bandwidth_hz ?? null} sr={m.quality?.sample_rate ?? null} />
                      </div>
                    </Tooltip>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 py-6 text-center text-[13px] text-fg-3">{suspects.isLoading ? t('process.qualityViz.loading') : t('process.qualityViz.noSuspects')}</p>
          )}
        </div>
      </Card>
    </div>
  )
}
