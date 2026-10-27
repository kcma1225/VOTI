import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Download, PackageOpen, RotateCcw, Save } from 'lucide-react'
import { api } from '@/api/client'
import { onErr, qk, useChannels, useDataset, useExports } from '@/api/queries'
import type { DatasetFilters, DatasetPreview, ReviewStatus, SpeakerState } from '@/api/types'
import { fmt1, fmtHours, fmtInt } from '@/lib/format'
import { ageGroupLabel, ageGroupOrder, flagLabel, reviewStatus, sexLabel, speakerState } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { Checkbox, Chip, Field, Input, NumberInput, Segmented, Slider, Switch, Textarea } from '@/components/ui/form'
import { Dialog } from '@/components/ui/overlay'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { HBars, SERIES, StackBar } from '@/components/charts/charts'
import { DEFAULT_FILTERS, ExportRow } from './common'

function useDebounced<T>(v: T, ms: number) {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="border-t border-border px-4 py-5 first:border-t-0 sm:px-5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {description && <p className="mt-0.5 text-xs leading-5 text-fg-3">{description}</p>}
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  )
}

function toggle<T>(xs: T[], x: T) {
  return xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x]
}

function ThresholdField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  unit,
  defaultOn,
  hint,
}: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
  min: number
  max: number
  step: number
  unit?: string
  defaultOn: number
  hint?: string
}) {
  const on = value !== null
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <Checkbox checked={on} onCheckedChange={(v) => onChange(v ? defaultOn : null)} label={label} description={hint} />
        <span className={cn('shrink-0 text-[13px] font-medium tabular', on ? 'text-fg' : 'text-fg-3')}>
          {on ? `≥ ${step < 1 ? value!.toFixed(step < 0.1 ? 2 : 1) : fmtInt(value)}${unit ? ` ${unit}` : ''}` : '不限'}
        </span>
      </div>
      {on && <Slider aria-label={label} value={[value!]} onValueChange={([v]) => onChange(v)} min={min} max={max} step={step} className="pl-7" />}
    </div>
  )
}

function PreviewPanel({ p, loading, error }: { p?: DatasetPreview; loading: boolean; error: unknown }) {
  if (error) return <ErrorState error={error} />
  if (!p)
    return (
      <div className="space-y-3 p-5">
        <Skeleton className="h-12 w-40" />
        <Skeleton className="h-24" />
      </div>
    )
  const ex = Object.entries(p.excluded).filter(([, v]) => v > 0)
  const exLabel: Record<string, string> = { repeated: '重複片段', auto_caption: '自動字幕', low_quality: '音質或長度不符', review: '審查/語者狀態', speaker_cap: '超過語者上限' }
  return (
    <div className={cn('transition-opacity', loading && 'opacity-60')}>
      <CardBody className="space-y-5">
        <div>
          <div className="text-xs text-fg-3">預估可匯出</div>
          <div className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-4xl font-semibold tracking-tight">{fmt1(p.total_sec / 3600)}</span>
            <span className="text-sm text-fg-3">小時</span>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            {[
              ['音段', fmtInt(p.segment_count)],
              ['語者', fmtInt(p.speakers)],
              ['頻道', fmtInt(p.channels)],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg bg-surface-2/70 py-2">
                <div className="text-[15px] font-semibold">{v}</div>
                <div className="text-[11px] text-fg-3">{k}</div>
              </div>
            ))}
          </div>
        </div>
        {p.total_sec > 0 && (
          <div>
            <div className="mb-2 text-xs font-medium text-fg-3">性別（Vox-Profile 推估）</div>
            <StackBar
              format={fmtHours}
              parts={[
                { key: 'male', label: sexLabel.male, value: p.by_sex.male ?? 0, color: 'var(--series-1)' },
                { key: 'female', label: sexLabel.female, value: p.by_sex.female ?? 0, color: 'var(--series-2)' },
                { key: 'unknown', label: sexLabel.unknown, value: p.by_sex.unknown ?? 0, color: 'var(--series-muted)' },
              ]}
            />
          </div>
        )}
        {p.by_channel.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-medium text-fg-3">各頻道時數</div>
            <HBars rows={p.by_channel.map((c) => ({ key: c.channel_id, label: c.title, value: c.total_sec }))} format={fmtHours} />
          </div>
        )}
        <div>
          <div className="mb-2 text-xs font-medium text-fg-3">被排除的音段</div>
          {ex.length ? (
            <HBars rows={ex.map(([k, v], i) => ({ key: k, label: exLabel[k] ?? k, value: v, color: SERIES[[1, 3, 7, 4, 2][i % 5]] }))} format={(v) => `${fmtInt(v)} 段`} />
          ) : (
            <p className="text-[13px] text-fg-3">沒有音段被排除</p>
          )}
        </div>
      </CardBody>
    </div>
  )
}

export default function DatasetBuilder() {
  const { id } = useParams()
  const isNew = !id
  const ds = useDataset(id)
  const exports = useExports(id)
  const channels = useChannels()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [f, setF] = useState<DatasetFilters>(DEFAULT_FILTERS)
  const [exportOpen, setExportOpen] = useState(false)
  const [fmt, setFmt] = useState<'jsonl' | 'csv'>('jsonl')
  const [withAudio, setWithAudio] = useState(true)
  useTitle(isNew ? '建立資料集' : ds.data?.name)

  useEffect(() => {
    if (ds.data) {
      setName(ds.data.name)
      setDesc(ds.data.description)
      setF({ ...DEFAULT_FILTERS, ...ds.data.filters })
    }
  }, [ds.data])

  const df = useDebounced(f, 350)
  const preview = useQuery({ queryKey: ['preview', df], queryFn: () => api.previewDataset(df), placeholderData: keepPreviousData })
  const upd = (patch: Partial<DatasetFilters>) => setF((o) => ({ ...o, ...patch }))
  const dirty = !!ds.data && (JSON.stringify({ ...DEFAULT_FILTERS, ...ds.data.filters }) !== JSON.stringify(f) || ds.data.name !== name || ds.data.description !== desc)

  const save = useMutation({
    mutationFn: () => (isNew ? api.createDataset({ name: name.trim(), description: desc, filters: f }) : api.patchDataset(id!, { name: name.trim(), description: desc, filters: f })),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: qk.datasets })
      qc.setQueryData(qk.dataset(d.id), d)
      toast.success(isNew ? '已建立資料集' : '已儲存')
      if (isNew) navigate(`/datasets/${d.id}`, { replace: true })
    },
    onError: onErr,
  })
  const doExport = useMutation({
    mutationFn: () => api.exportDataset(id!, fmt, withAudio),
    onSuccess: () => {
      toast.success('已開始匯出', { description: '完成後可在下方下載 manifest 與音檔壓縮檔。' })
      qc.invalidateQueries({ queryKey: qk.exports(id!) })
      setExportOpen(false)
    },
    onError: onErr,
  })

  if (!isNew && ds.isError)
    return (
      <Page>
        <Card>
          <ErrorState error={ds.error} onRetry={() => ds.refetch()} />
        </Card>
      </Page>
    )

  const includeAuto = f.text_sources.includes('auto_caption')
  const p = preview.data
  return (
    <Page wide>
      <Link to="/datasets" className="-ml-1 mb-3 inline-flex h-8 items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 hover:bg-surface-2 hover:text-fg">
        ‹ 資料集
      </Link>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold leading-8 tracking-tight sm:text-2xl">{isNew ? '建立資料集' : name || '資料集'}</h1>
          <p className="mt-1 text-[13px] text-fg-3">調整條件時，預估時數會即時更新。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setF(DEFAULT_FILTERS)}>
            <RotateCcw /> 預設條件
          </Button>
          <Button variant={isNew || dirty ? 'primary' : 'secondary'} onClick={() => save.mutate()} loading={save.isPending} disabled={!name.trim() || (!isNew && !dirty)}>
            <Save /> {isNew ? '建立' : '儲存'}
          </Button>
          {!isNew && (
            <Button variant="primary" onClick={() => setExportOpen(true)} disabled={dirty}>
              <Download /> 匯出…
            </Button>
          )}
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-4">
          <Card>
            <CardBody className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
              <Field label="名稱" htmlFor="ds-name">
                <Input id="ds-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：台語 ASR 訓練集 v2" />
              </Field>
              <Field label="說明" htmlFor="ds-desc">
                <Textarea id="ds-desc" value={desc} onChange={(e) => setDesc(e.target.value)} rows={1} className="min-h-10 sm:min-h-9" placeholder="用途、版本或注意事項" />
              </Field>
            </CardBody>
          </Card>

          {/* compact preview on < xl */}
          <Card className="xl:hidden">
            <CardHeader title="即時預覽" />
            <PreviewPanel p={p} loading={preview.isFetching} error={preview.error} />
          </Card>

          <Card>
            <Section title="來源" description="不選代表全部頻道。">
              <div className="flex flex-wrap gap-1.5">
                {channels.data?.items.map((c) => (
                  <Chip key={c.id} active={f.channel_ids.includes(c.id)} onClick={() => upd({ channel_ids: toggle(f.channel_ids, c.id) })}>
                    {c.title}
                  </Chip>
                ))}
              </div>
              <Field label="授權">
                <Segmented
                  value={f.licenses.length === 1 ? f.licenses[0] : 'all'}
                  onChange={(v) => upd({ licenses: v === 'all' ? [] : [v as 'youtube' | 'creative_commons'] })}
                  options={[
                    { value: 'all', label: '全部' },
                    { value: 'creative_commons', label: '僅 CC 授權' },
                    { value: 'youtube', label: '僅標準授權' },
                  ]}
                />
              </Field>
            </Section>

            <Section title="文字與審查" description="預設只用上傳者提供的原生字幕與人工校正文字。">
              <div className="grid gap-1 sm:grid-cols-2">
                <Checkbox checked={f.text_sources.includes('manual_caption')} onCheckedChange={() => upd({ text_sources: toggle(f.text_sources, 'manual_caption') })} label="原生字幕" />
                <Checkbox checked={f.text_sources.includes('human_revision')} onCheckedChange={() => upd({ text_sources: toggle(f.text_sources, 'human_revision') })} label="人工校正" />
              </div>
              <div className={cn('rounded-xl border p-3.5', includeAuto ? 'border-warning/40 bg-warning-soft/60' : 'border-border')}>
                <Switch checked={includeAuto} onCheckedChange={() => upd({ text_sources: toggle(f.text_sources, 'auto_caption') })} label="包含 YouTube 自動字幕" description="自動語音辨識的文字對台語錯誤率高，建議只在人工校正後使用。" />
                {includeAuto && (
                  <p className="mt-2 flex items-start gap-1.5 text-xs text-warning-fg">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> 匯出的 manifest 會以 text_source 標示這些音段。
                  </p>
                )}
              </div>
              <Field label="審查狀態">
                <div className="flex flex-wrap gap-1.5">
                  {(['passed', 'unchecked', 'needs_fix'] as ReviewStatus[]).map((r) => (
                    <Chip key={r} active={f.review_status.includes(r)} onClick={() => upd({ review_status: toggle(f.review_status, r) })}>
                      {reviewStatus[r].label}
                    </Chip>
                  ))}
                </div>
              </Field>
            </Section>

            <Section title="音質門檻">
              <div className="grid gap-x-8 gap-y-5 lg:grid-cols-2">
                <ThresholdField label="SNR" unit="dB" value={f.min_snr} onChange={(v) => upd({ min_snr: v })} min={0} max={40} step={1} defaultOn={10} />
                <ThresholdField label="DNSMOS OVRL" value={f.min_dnsmos_ovrl} onChange={(v) => upd({ min_dnsmos_ovrl: v })} min={1} max={4.5} step={0.1} defaultOn={2.8} />
                <ThresholdField label="DNSMOS P.808" value={f.min_dnsmos_p808} onChange={(v) => upd({ min_dnsmos_p808: v })} min={1} max={4.5} step={0.1} defaultOn={3.0} />
                <ThresholdField label="C50" unit="dB" value={f.min_c50} onChange={(v) => upd({ min_c50: v })} min={0} max={60} step={1} defaultOn={20} hint="越高殘響越少" />
                <ThresholdField label="語音比例" value={f.min_speech_ratio} onChange={(v) => upd({ min_speech_ratio: v })} min={0} max={1} step={0.05} defaultOn={0.6} />
                <ThresholdField label="實際頻寬" unit="Hz" value={f.min_bandwidth_hz} onChange={(v) => upd({ min_bandwidth_hz: v })} min={3000} max={20000} step={500} defaultOn={7000} />
              </div>
              <Checkbox checked={f.exclude_upsampled} onCheckedChange={(v) => upd({ exclude_upsampled: v })} label="排除疑似升頻的影片" description="實際頻寬遠低於取樣率的一半。" />
              <Field label="排除旗標">
                <div className="flex flex-wrap gap-1.5">
                  {['clipped', 'low_snr', 'low_speech', 'overlap', 'too_short'].map((fl) => (
                    <Chip key={fl} active={f.exclude_flags.includes(fl)} onClick={() => upd({ exclude_flags: toggle(f.exclude_flags, fl) })}>
                      {flagLabel[fl]}
                    </Chip>
                  ))}
                </div>
              </Field>
            </Section>

            <Section title="長度與重複片段">
              <div className="grid grid-cols-2 gap-3 sm:max-w-sm">
                <Field label="最短">
                  <NumberInput value={f.min_duration} onChange={(v) => upd({ min_duration: v })} min={0} step={0.5} suffix="秒" />
                </Field>
                <Field label="最長">
                  <NumberInput value={f.max_duration} onChange={(v) => upd({ max_duration: v })} min={0} step={0.5} suffix="秒" />
                </Field>
              </div>
              <Switch checked={f.exclude_repeated} onCheckedChange={(v) => upd({ exclude_repeated: v })} label="排除重複的片頭、片尾與固定橋段" description="依各頻道「重複片段」頁中被排除的群組計算。" />
              {f.exclude_repeated && (
                <Field label="可容許的重疊比例" aside={`≤ ${Math.round(f.max_repeated_overlap * 100)}%`}>
                  <Slider aria-label="重疊比例" value={[f.max_repeated_overlap]} onValueChange={([v]) => upd({ max_repeated_overlap: v })} min={0} max={1} step={0.05} />
                </Field>
              )}
            </Section>

            <Section title="語者" description="控制語者多樣性：設定每位語者的時數上限，避免少數主持人佔滿資料集。">
              <Field label="語者狀態">
                <div className="flex flex-wrap gap-1.5">
                  {(['single', 'unknown', 'multiple', 'overlap'] as SpeakerState[]).map((s) => (
                    <Chip key={s} active={f.speaker_states.includes(s)} onClick={() => upd({ speaker_states: toggle(f.speaker_states, s) })}>
                      {speakerState[s]}
                    </Chip>
                  ))}
                </div>
              </Field>
              <div className="grid gap-4 lg:grid-cols-2">
                <Field label="性別（推估）">
                  <div className="flex flex-wrap gap-1.5">
                    {['male', 'female'].map((s) => (
                      <Chip key={s} active={f.sex.includes(s)} onClick={() => upd({ sex: toggle(f.sex, s) })}>
                        {sexLabel[s]}
                      </Chip>
                    ))}
                  </div>
                </Field>
                <Field label="每位語者上限" hint="不填代表不限">
                  <NumberInput value={f.max_sec_per_speaker != null ? Math.round(f.max_sec_per_speaker / 60) : null} onChange={(v) => upd({ max_sec_per_speaker: v != null ? v * 60 : null })} min={1} suffix="分鐘" />
                </Field>
              </div>
              <Field label="年齡組（推估）">
                <div className="flex flex-wrap gap-1.5">
                  {ageGroupOrder.map((g) => (
                    <Chip key={g} active={f.age_groups.includes(g)} onClick={() => upd({ age_groups: toggle(f.age_groups, g) })}>
                      {ageGroupLabel(g)}
                    </Chip>
                  ))}
                </div>
              </Field>
            </Section>
          </Card>

          {!isNew && (
            <Card className="overflow-clip">
              <CardHeader
                title="匯出紀錄"
                description="每次匯出都是當時的快照；manifest 含 segment_id、revision 與 sha256。"
                actions={
                  <Button size="sm" onClick={() => setExportOpen(true)} disabled={dirty}>
                    <Download /> 新匯出
                  </Button>
                }
              />
              <div className="mt-3 divide-y divide-border border-t border-border">
                {!exports.data ? (
                  <Skeleton className="m-4 h-12" />
                ) : exports.data.length ? (
                  exports.data.map((e) => <ExportRow key={e.id} e={e} />)
                ) : (
                  <EmptyState compact icon={<PackageOpen />} title="尚未匯出" description="儲存條件後即可匯出 JSONL 或 CSV 與音檔。" />
                )}
              </div>
            </Card>
          )}
        </div>

        <aside className="hidden xl:block">
          <Card className="sticky top-6">
            <CardHeader title="即時預覽" description="依目前條件計算" />
            <PreviewPanel p={p} loading={preview.isFetching} error={preview.error} />
          </Card>
        </aside>
      </div>

      {/* sticky summary on < xl */}
      {p && (
        <div className="sticky bottom-[calc(64px+env(safe-area-inset-bottom))] z-20 -mx-4 mt-4 flex items-center justify-between gap-3 border-t border-border bg-surface/95 px-4 py-3 backdrop-blur-xl sm:-mx-6 sm:px-6 md:bottom-0 xl:hidden">
          <div className="min-w-0 text-[13px]">
            <span className="text-lg font-semibold">{fmt1(p.total_sec / 3600)}</span> 小時
            <span className="ml-2 text-fg-3">
              {fmtInt(p.segment_count)} 段 · {fmtInt(p.speakers)} 位語者
            </span>
          </div>
          <Button variant="primary" size="sm" onClick={() => save.mutate()} loading={save.isPending} disabled={!name.trim() || (!isNew && !dirty)}>
            <Save /> {isNew ? '建立' : '儲存'}
          </Button>
        </div>
      )}

      <Dialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        title="匯出資料集"
        description={p ? `將匯出 ${fmtInt(p.segment_count)} 個音段，共 ${fmtHours(p.total_sec)}。` : undefined}
        footer={
          <>
            <Button onClick={() => setExportOpen(false)}>取消</Button>
            <Button variant="primary" onClick={() => doExport.mutate()} loading={doExport.isPending}>
              <Download /> 開始匯出
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <Field label="Manifest 格式">
            <Segmented
              value={fmt}
              onChange={setFmt}
              options={[
                { value: 'jsonl', label: 'JSONL' },
                { value: 'csv', label: 'CSV' },
              ]}
            />
          </Field>
          <Switch checked={withAudio} onCheckedChange={setWithAudio} label="包含音檔（dataset.tar）" description="16 kHz 單聲道 WAV，依 manifest 的 audio_path 存放。" />
        </div>
      </Dialog>
    </Page>
  )
}
