import { useNavigate } from 'react-router'
import { BadgeCheck, Info } from 'lucide-react'
import type { Dist, Speaker, VoiceProfile } from '@/api/types'
import { fmtHours, fmtInt } from '@/lib/format'
import { ageGroupLabel, emotionLabel, sexLabel, speechFlowLabel, voiceQualityLabel } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { DataTable, type Column } from '@/components/ui/data-table'
import { Avatar, Badge } from '@/components/ui/primitives'
import { Tooltip } from '@/components/ui/overlay'

export const topOf = (d?: Dist | null) => {
  if (!d) return null
  let k: string | null = null
  let v = -1
  for (const [kk, vv] of Object.entries(d)) if (vv > v) [k, v] = [kk, vv]
  return k ? { key: k, value: v } : null
}

export function speakerName(s: { label: string; display_name: string | null }) {
  return s.display_name || s.label
}

export function SexSplit({ d, className }: { d?: Dist; className?: string }) {
  const m = d?.male ?? 0
  const f = d?.female ?? 0
  const tot = m + f || 1
  const lead = m >= f ? 'male' : 'female'
  return (
    <Tooltip content={`男 ${Math.round((m / tot) * 100)}% · 女 ${Math.round((f / tot) * 100)}%（Vox-Profile 推估）`}>
      <div className={cn('flex min-w-0 items-center gap-2', className)}>
        <div className="flex h-1.5 w-14 shrink-0 gap-px overflow-hidden rounded-full">
          <span style={{ width: `${(m / tot) * 100}%`, background: 'var(--series-1)' }} />
          <span style={{ width: `${(f / tot) * 100}%`, background: 'var(--series-2)' }} />
        </div>
        <span className="truncate text-xs text-fg-2">{sexLabel[lead]}</span>
      </div>
    </Tooltip>
  )
}

export function SpeakerList({ items, className }: { items: Speaker[]; className?: string }) {
  const navigate = useNavigate()
  const max = Math.max(1, ...items.map((s) => s.total_sec))
  const columns: Column<Speaker>[] = [
    {
      id: 'name',
      header: '語者',
      cell: (s) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={speakerName(s)} size={32} />
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-[13px] font-medium">{speakerName(s)}</span>
              {s.verified && <BadgeCheck className="size-4 shrink-0 text-primary" aria-label="已確認" />}
            </div>
            <div className="truncate font-mono text-[11px] text-fg-3">{s.label}</div>
          </div>
        </div>
      ),
    },
    {
      id: 'hours',
      header: '時數',
      width: 'w-44',
      cell: (s) => (
        <div className="flex items-center gap-2">
          <div className="h-1.5 min-w-0 flex-1 rounded-full bg-surface-3">
            <div className="h-full rounded-full bg-series-1" style={{ width: `${(s.total_sec / max) * 100}%` }} />
          </div>
          <span className="w-16 shrink-0 text-right text-xs tabular text-fg-2">{fmtHours(s.total_sec)}</span>
        </div>
      ),
    },
    { id: 'segs', header: '音段', width: 'w-20', align: 'right', cell: (s) => <span className="text-fg-2">{fmtInt(s.segment_count)}</span> },
    { id: 'media', header: '影片', width: 'w-16', align: 'right', hideBelow: 'xl', cell: (s) => <span className="text-fg-2">{fmtInt(s.media_count)}</span> },
    { id: 'sex', header: '性別', width: 'w-28', cell: (s) => <SexSplit d={s.profile.sex} /> },
    { id: 'age', header: '年齡', width: 'w-24', hideBelow: 'lg', cell: (s) => <span className="text-fg-2">{s.profile.age_mean ? `約 ${Math.round(s.profile.age_mean)} 歲` : '—'}</span> },
    {
      id: 'emo',
      header: '主要情緒',
      width: 'w-24',
      hideBelow: 'xl',
      cell: (s) => {
        const t = topOf(s.profile.emotion)
        return <span className="text-fg-2">{t ? emotionLabel[t.key] ?? t.key : '—'}</span>
      },
    },
  ]
  return (
    <DataTable
      className={className}
      rows={items}
      columns={columns}
      onRowClick={(s) => navigate(`/speakers/${s.id}`)}
      card={(s) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={speakerName(s)} size={40} />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-sm font-medium">{speakerName(s)}</span>
              {s.verified && <BadgeCheck className="size-4 shrink-0 text-primary" />}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-3">
              <span className="tabular">{fmtHours(s.total_sec)}</span>
              <span className="tabular">{fmtInt(s.segment_count)} 段</span>
              <SexSplit d={s.profile.sex} />
              {s.profile.age_mean ? <span>約 {Math.round(s.profile.age_mean)} 歲</span> : null}
            </div>
          </div>
        </div>
      )}
    />
  )
}

/* ───────────── Vox-Profile traits ───────────── */
function DistBars({ d, labels, max = 4 }: { d: Dist; labels: Record<string, string>; max?: number }) {
  const entries = Object.entries(d)
    .sort((a, b) => b[1] - a[1])
    .slice(0, max)
  const top = entries[0]?.[1] ?? 1
  return (
    <ul className="space-y-1.5">
      {entries.map(([k, v], i) => (
        <li key={k} className="grid grid-cols-[4.5rem_minmax(0,1fr)_2.5rem] items-center gap-2 text-xs">
          <span className={cn('truncate', i === 0 ? 'font-medium text-fg' : 'text-fg-2')}>{labels[k] ?? k}</span>
          <div className="h-1.5 rounded-full bg-surface-3">
            <div className="h-full rounded-full" style={{ width: `${(v / (top || 1)) * 100}%`, background: i === 0 ? 'var(--series-1)' : 'var(--seq-3)' }} />
          </div>
          <span className="text-right tabular text-fg-3">{Math.round(v * 100)}%</span>
        </li>
      ))}
    </ul>
  )
}

export function ProfileTraits({ p, compact }: { p: VoiceProfile; compact?: boolean }) {
  const sexTop = p.sex
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <div className="rounded-lg bg-surface-2/70 px-3 py-2">
          <div className="text-[11px] text-fg-3">性別</div>
          <div className="text-sm font-semibold">
            {sexTop ? sexLabel[sexTop] : '—'}
            {p.sex_prob != null && <span className="ml-1 text-xs font-normal text-fg-3">{Math.round(p.sex_prob * 100)}%</span>}
          </div>
        </div>
        <div className="rounded-lg bg-surface-2/70 px-3 py-2">
          <div className="text-[11px] text-fg-3">年齡</div>
          <div className="text-sm font-semibold">
            {p.age != null ? `約 ${Math.round(p.age)} 歲` : '—'}
            {p.age_group && <span className="ml-1 text-xs font-normal text-fg-3">{ageGroupLabel(p.age_group)}</span>}
          </div>
        </div>
      </div>
      <div className={cn('grid gap-4', compact ? 'grid-cols-1' : 'sm:grid-cols-2')}>
        <div className="min-w-0">
          <div className="mb-1.5 text-[11px] font-medium text-fg-3">情緒</div>
          <DistBars d={p.emotion} labels={emotionLabel} />
        </div>
        <div className="min-w-0">
          <div className="mb-1.5 text-[11px] font-medium text-fg-3">音質</div>
          <DistBars d={p.voice_quality} labels={voiceQualityLabel} />
        </div>
        <div className="min-w-0">
          <div className="mb-1.5 text-[11px] font-medium text-fg-3">語流</div>
          <DistBars d={p.speech_flow} labels={speechFlowLabel} max={3} />
        </div>
        <div className="min-w-0">
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-fg-3">
            口音
            <Badge tone="warning" className="h-[18px] px-1.5 text-[10px]">
              僅供參考
            </Badge>
          </div>
          <DistBars d={p.accent_narrow} labels={{}} max={3} />
          <p className="mt-1.5 flex gap-1 text-[11px] leading-4 text-fg-3">
            <Info className="mt-px size-3 shrink-0" /> 口音模型只以英語訓練，用在台語上不可靠。
          </p>
        </div>
      </div>
      <div className="text-[11px] text-fg-3">Vox-Profile · {p.backbone}</div>
    </div>
  )
}
