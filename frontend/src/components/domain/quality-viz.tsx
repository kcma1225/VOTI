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
          <span>實際 {fmtKHz(bw)}</span>
          <span>奈奎斯特 {fmtKHz(nyq)}</span>
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
        <svg width={w} height={height} className="block" role="img" aria-label="頻譜">
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
            實際頻寬 {fmtKHz(bw)}
          </text>
          <line x1={padL} x2={w} y1={plotH} y2={plotH} stroke="var(--chart-axis)" />
          {ticks.map((t) => (
            <text key={t} x={X(t)} y={height - 6} textAnchor={t === 0 ? 'start' : t === nyq ? 'end' : 'middle'} className="fill-fg-3 text-[10px] tabular">
              {fmt1(t / 1000)}k
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
        <EmptyState icon={<Gauge />} title="還沒有音質資料" description="影片下載並完成音質分析後，SNR、DNSMOS、頻寬等分佈會出現在這裡。" />
      </Card>
    )
  const srs = Object.entries(d.sample_rates).sort((a, b) => b[1] - a[1])
  const codecs = Object.entries(d.codecs).sort((a, b) => b[1] - a[1])
  const tableOf = (bins: HistBin[], label: (b: number) => string) => ({ columns: ['區間', '影片數'], rows: bins.map((b) => [label(b.bin), b.count]) })
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="p-4">
          <Stat label="已分析影片" value={fmtInt(n)} unit="部" />
        </Card>
        <Card className="p-4">
          <Stat
            label="疑似升頻"
            value={fmtInt(d.upsampled_suspect)}
            unit="部"
            hint={`${fmtPct(d.upsampled_suspect / n, 1)} · 頻寬遠低於取樣率`}
            icon={d.upsampled_suspect ? <AlertTriangle className="text-warning" /> : undefined}
          />
        </Card>
        <Card className="p-4">
          <Stat label="主要取樣率" value={srs[0] ? fmt1(Number(srs[0][0]) / 1000) : '—'} unit="kHz" hint={srs[0] ? `${fmtPct(srs[0][1] / n)} 的影片` : undefined} />
        </Card>
        <Card className="p-4">
          <Stat label="主要編碼" value={codecs[0]?.[0] ?? '—'} hint={codecs[0] ? `${fmtPct(codecs[0][1] / n)} 的影片` : undefined} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="訊噪比 SNR" description={`語音段平均（dB）；直線為低 SNR 門檻 ${snrTh} dB`} table={tableOf(d.histograms.snr, (b) => `${b} dB`)}>
          <Histogram bins={hz(d.histograms.snr)} binLabel={(b) => `${b}`} threshold={{ value: snrTh, label: `${snrTh} dB`, binWidth: 2.5, start: 0 }} highlight={(b) => b >= snrTh - 1e-9} />
        </ChartCard>
        <ChartCard title="DNSMOS 整體品質（OVRL）" description="1–5 分，越高越乾淨" table={tableOf(d.histograms.dnsmos_ovrl, (b) => b.toFixed(1))}>
          <Histogram bins={hz(d.histograms.dnsmos_ovrl)} binLabel={(b) => b.toFixed(1)} />
        </ChartCard>
        <ChartCard title="實際頻寬" description="原始音訊頻譜的有效上限；明顯低於奈奎斯特代表升頻" table={tableOf(d.histograms.bandwidth_hz, (b) => `${b / 1000} kHz`)}>
          <Histogram bins={hz(d.histograms.bandwidth_hz)} binLabel={(b) => `${b / 1000}k`} highlight={(b) => b >= 7000} />
        </ChartCard>
        <ChartCard title="語音比例" description={`VAD 判定為語音的時間比例；門檻 ${fmtPct(srTh)}`} table={tableOf(d.histograms.speech_ratio, (b) => fmtPct(b))}>
          <Histogram bins={hz(d.histograms.speech_ratio)} binLabel={(b) => `${Math.round(b * 100)}%`} threshold={{ value: srTh, label: fmtPct(srTh), binWidth: 0.05, start: 0 }} highlight={(b) => b >= srTh - 1e-9} />
        </ChartCard>
        <ChartCard title="殘響 C50" description="早期/晚期能量比（dB），越高越乾" table={tableOf(d.histograms.c50, (b) => `${b} dB`)}>
          <Histogram bins={hz(d.histograms.c50)} binLabel={(b) => `${b}`} />
        </ChartCard>
        <Card className="flex flex-col">
          <CardHeader title="取樣率與編碼" description="原始下載檔（ffprobe）" />
          <div className="grid flex-1 gap-6 px-4 pb-5 pt-4 sm:grid-cols-2 sm:px-5">
            <div className="min-w-0">
              <div className="mb-3 text-xs font-medium text-fg-3">取樣率</div>
              <HBars rows={srs.map(([k, v], i) => ({ key: k, label: `${fmt1(Number(k) / 1000)} kHz`, value: v, color: SERIES[i] }))} format={(v) => `${fmtInt(v)} 部`} />
            </div>
            <div className="min-w-0">
              <div className="mb-3 text-xs font-medium text-fg-3">編碼</div>
              <HBars rows={codecs.map(([k, v], i) => ({ key: k, label: k, value: v, color: SERIES[i] }))} format={(v) => `${fmtInt(v)} 部`} />
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="疑似升頻的影片"
          description="取樣率很高，但實際頻寬明顯偏低（例如 8 kHz 內容存成 44.1 kHz）。這類音訊不適合需要高頻細節的用途。"
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
                    <Tooltip content="橘色：實際頻寬；右側刻度：奈奎斯特頻率">
                      <div>
                        <BandwidthBar bw={m.quality?.real_bandwidth_hz ?? null} sr={m.quality?.sample_rate ?? null} />
                      </div>
                    </Tooltip>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 py-6 text-center text-[13px] text-fg-3">{suspects.isLoading ? '載入中…' : '沒有疑似升頻的影片'}</p>
          )}
        </div>
      </Card>
    </div>
  )
}
