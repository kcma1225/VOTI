import { CheckCircle2, Download, FileText, Loader2, XCircle } from 'lucide-react'
import { audioUrl } from '@/api/client'
import type { DatasetExport, DatasetFilters } from '@/api/types'
import { fmtBytes, fmtHours, fmtInt, fmtRelative } from '@/lib/format'
import { Badge, Progress, Tag } from '@/components/ui/primitives'
import { reviewStatus, textSource } from '@/lib/labels'

export const DEFAULT_FILTERS: DatasetFilters = {
  channel_ids: [],
  media_ids: [],
  speaker_ids: [],
  text_sources: ['manual_caption', 'human_revision'],
  review_status: ['passed', 'unchecked'],
  exclude_repeated: true,
  max_repeated_overlap: 0.2,
  speaker_states: ['single', 'unknown'],
  min_duration: 1.0,
  max_duration: 20.0,
  min_snr: 10,
  min_c50: null,
  min_dnsmos_ovrl: 2.8,
  min_dnsmos_p808: null,
  min_speech_ratio: 0.6,
  min_bandwidth_hz: 7000,
  exclude_upsampled: false,
  exclude_flags: ['clipped'],
  licenses: [],
  sex: [],
  age_groups: [],
  max_sec_per_speaker: null,
}

export function filterSummary(f: DatasetFilters): string[] {
  const out: string[] = []
  out.push(f.text_sources.map((t) => textSource[t] ?? t).join(' + ') || '任何文字')
  if (!f.text_sources.includes('auto_caption')) out.push('排除自動字幕')
  if (f.review_status.length) out.push(f.review_status.map((r) => reviewStatus[r].label).join('/'))
  if (f.exclude_repeated) out.push(`排除重複 > ${Math.round(f.max_repeated_overlap * 100)}%`)
  if (f.min_snr != null) out.push(`SNR ≥ ${f.min_snr} dB`)
  if (f.min_dnsmos_ovrl != null) out.push(`DNSMOS ≥ ${f.min_dnsmos_ovrl}`)
  if (f.min_bandwidth_hz != null) out.push(`頻寬 ≥ ${f.min_bandwidth_hz / 1000} kHz`)
  if (f.exclude_upsampled) out.push('排除升頻')
  if (f.age_groups.length) out.push(`年齡 ${f.age_groups.join('、')}`)
  if (f.max_sec_per_speaker) out.push(`每位語者 ≤ ${Math.round(f.max_sec_per_speaker / 60)} 分`)
  if (f.channel_ids.length) out.push(`${f.channel_ids.length} 個頻道`)
  return out
}

export function FilterTags({ f, max = 6 }: { f: DatasetFilters; max?: number }) {
  const xs = filterSummary(f)
  return (
    <div className="flex flex-wrap gap-1">
      {xs.slice(0, max).map((x) => (
        <Tag key={x}>{x}</Tag>
      ))}
      {xs.length > max && <Tag>+{xs.length - max}</Tag>}
    </div>
  )
}

export function ExportRow({ e }: { e: DatasetExport }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4 sm:px-5">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        {e.status === 'completed' ? (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
        ) : e.status === 'failed' ? (
          <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />
        ) : (
          <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="font-medium">{(e.format ?? 'jsonl').toUpperCase()}</span>
            {e.include_audio !== false && <Tag>含音檔</Tag>}
            <span className="text-fg-3">{fmtRelative(e.created_at)}</span>
          </div>
          <div className="mt-0.5 text-xs text-fg-3">
            {e.status === 'failed'
              ? '匯出失敗，請重試'
              : `${fmtInt(e.segment_count)} 段 · ${fmtHours(e.total_sec)}${e.size_bytes ? ` · ${fmtBytes(e.size_bytes)}` : ''}`}
          </div>
          {(e.status === 'running' || e.status === 'queued') && <Progress className="mt-2 max-w-xs" value={e.progress ?? 0} indeterminate={e.status === 'queued'} />}
        </div>
      </div>
      {e.status === 'completed' ? (
        <div className="flex flex-wrap gap-1.5 pl-7 sm:pl-0">
          {(e.files ?? ['manifest.jsonl', 'README.md', 'dataset.tar']).map((f) => (
            <a
              key={f}
              href={audioUrl.exportFile(e.id, f)}
              download
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 text-xs font-medium text-fg-2 hover:border-border-strong hover:text-fg sm:h-7"
            >
              {f.endsWith('.tar') ? <Download className="size-3.5" /> : <FileText className="size-3.5" />}
              {f}
            </a>
          ))}
        </div>
      ) : (
        <Badge tone={e.status === 'failed' ? 'danger' : 'primary'} dot className="ml-7 self-start sm:ml-0 sm:self-center">
          {e.status === 'failed' ? '失敗' : e.status === 'queued' ? '排隊中' : `匯出中 ${Math.round((e.progress ?? 0) * 100)}%`}
        </Badge>
      )}
    </div>
  )
}
