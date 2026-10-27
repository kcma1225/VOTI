import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Captions, Download, Film, ListFilter, Smartphone, X } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, useMedia, usePlaylists } from '@/api/queries'
import type { CaptionFilter, MediaKind, MediaQuery, MediaSort, MediaStatus, MediaSummary } from '@/api/types'
import { fmtClock, fmtCompact, fmtDate, fmtDb, fmtHours, fmtInt, fmtMos } from '@/lib/format'
import { lang, licenseLabel, mediaStatus } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useIsDesktop } from '@/hooks/useMedia'
import { Button } from '@/components/ui/button'
import { Card, Tag, Thumb } from '@/components/ui/primitives'
import { Checkbox, Chip, Field, Input, NumberInput, SearchInput, Segmented, Select, Switch } from '@/components/ui/form'
import { DataTable, Pagination, type Column } from '@/components/ui/data-table'
import { Dialog, Sheet } from '@/components/ui/overlay'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { CaptionBadges, MediaStatusBadge } from './status'

const LIMIT = 50
type CapUI = 'all' | 'manual' | 'none' | 'unknown'

/* ───────────── URL-backed filter state ───────────── */
function useMediaFilters(kind: MediaKind[]) {
  const [sp, setSp] = useSearchParams()
  const get = (k: string) => sp.get(k) ?? ''
  const f = {
    q: get('q'),
    cap: (get('cap') || 'all') as CapUI,
    auto: sp.get('auto') === '1',
    status: get('status') ? (get('status').split(',') as MediaStatus[]) : [],
    minDur: get('min') ? Number(get('min')) : null,
    maxDur: get('max') ? Number(get('max')) : null,
    after: get('after'),
    before: get('before'),
    license: get('license') as '' | 'youtube' | 'creative_commons',
    sort: (get('sort') || '-published_at') as MediaSort,
    playlist: get('playlist'),
    offset: Number(get('offset') || 0),
  }
  const set = (patch: Record<string, string | number | boolean | null | undefined>, keepOffset = false) => {
    const n = new URLSearchParams(sp)
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === undefined || v === '' || v === false) n.delete(k)
      else n.set(k, v === true ? '1' : String(v))
    }
    if (!keepOffset) n.delete('offset')
    setSp(n, { replace: true })
  }
  const query = (channel_id: string): MediaQuery => ({
    channel_id,
    kind,
    q: f.q || undefined,
    captions: f.cap === 'manual' ? 'manual' : f.cap === 'none' ? ('none' as CaptionFilter) : undefined,
    detailed: f.cap === 'unknown' ? false : undefined,
    include_auto: f.auto || undefined,
    status: f.status.length ? f.status : undefined,
    min_duration: f.minDur !== null ? f.minDur * 60 : undefined,
    max_duration: f.maxDur !== null ? f.maxDur * 60 : undefined,
    published_after: f.after || undefined,
    published_before: f.before || undefined,
    license: f.license || undefined,
    playlist_id: f.playlist || undefined,
    sort: f.sort,
  })
  const activeCount =
    (f.cap !== 'all' ? 1 : 0) + (f.auto ? 1 : 0) + (f.status.length ? 1 : 0) + (f.minDur !== null || f.maxDur !== null ? 1 : 0) + (f.after || f.before ? 1 : 0) + (f.license ? 1 : 0)
  return { f, set, query, activeCount, clear: () => setSp(new URLSearchParams(), { replace: true }) }
}

/* ───────────── filter panel ───────────── */
function FilterPanel({ ctl, facets }: { ctl: ReturnType<typeof useMediaFilters>; facets?: import('@/api/types').MediaFacets }) {
  const { f, set } = ctl
  const fc = facets?.captions
  const durPresets: [string, number | null, number | null][] = [
    ['< 1 分', null, 1],
    ['1–10 分', 1, 10],
    ['10–30 分', 10, 30],
    ['30 分以上', 30, null],
  ]
  const statuses: MediaStatus[] = ['listed', 'detailed', 'queued', 'downloading', 'processing', 'ready', 'failed']
  return (
    <div className="space-y-6">
      <section>
        <h4 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold">
          <Captions className="size-4 text-fg-3" /> 字幕
        </h4>
        <div className="space-y-1" role="radiogroup" aria-label="字幕">
          {(
            [
              ['all', '不限', undefined],
              ['manual', '有原生字幕', fc?.manual],
              ['none', '沒有任何字幕', fc?.none],
              ['unknown', '尚未檢查', fc?.unknown],
            ] as [CapUI, string, number | undefined][]
          ).map(([v, label, n]) => (
            <button
              key={v}
              role="radio"
              aria-checked={f.cap === v}
              onClick={() => set({ cap: v === 'all' ? null : v })}
              className={cn(
                'flex h-10 w-full items-center justify-between gap-2 rounded-lg px-3 text-left text-[13px] transition-colors sm:h-9',
                f.cap === v ? 'bg-primary-soft font-medium text-primary-soft-fg' : 'text-fg-2 hover:bg-surface-2',
              )}
            >
              <span className="flex items-center gap-2.5">
                <span className={cn('grid size-4 place-items-center rounded-full border', f.cap === v ? 'border-primary' : 'border-border-strong')}>
                  {f.cap === v && <span className="size-2 rounded-full bg-primary" />}
                </span>
                {label}
              </span>
              {n !== undefined && <span className="tabular text-xs text-fg-3">{fmtInt(n)}</span>}
            </button>
          ))}
        </div>
        <div className="mt-3 rounded-lg border border-border bg-surface-2/50 p-3">
          <Switch
            checked={f.auto}
            onCheckedChange={(v) => set({ auto: v })}
            label="包含僅有自動字幕的影片"
            description={`YouTube 語音辨識字幕品質不穩定，預設不列入。${fc ? `（${fmtInt(fc.auto_only)} 部）` : ''}`}
          />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">處理狀態</h4>
        <div className="flex flex-wrap gap-1.5">
          {statuses.map((s) => (
            <Chip
              key={s}
              active={f.status.includes(s)}
              count={facets?.status[s] ?? 0}
              onClick={() => set({ status: (f.status.includes(s) ? f.status.filter((x) => x !== s) : [...f.status, s]).join(',') })}
            >
              {mediaStatus[s].label}
            </Chip>
          ))}
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">長度</h4>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {durPresets.map(([label, a, b]) => (
            <Chip key={label} active={f.minDur === a && f.maxDur === b} onClick={() => (f.minDur === a && f.maxDur === b ? set({ min: null, max: null }) : set({ min: a, max: b }))}>
              {label}
            </Chip>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <NumberInput aria-label="最短（分鐘）" value={f.minDur} onChange={(v) => set({ min: v })} min={0} suffix="分↑" />
          <NumberInput aria-label="最長（分鐘）" value={f.maxDur} onChange={(v) => set({ max: v })} min={0} suffix="分↓" />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">發布日期</h4>
        <div className="grid grid-cols-2 gap-2">
          <Input type="date" aria-label="起" value={f.after} onChange={(e) => set({ after: e.target.value })} />
          <Input type="date" aria-label="迄" value={f.before} onChange={(e) => set({ before: e.target.value })} />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">授權</h4>
        <Segmented
          className="w-full [&>*]:flex-1"
          value={f.license || 'all'}
          onChange={(v) => set({ license: v === 'all' ? null : v })}
          options={[
            { value: 'all', label: '全部' },
            { value: 'youtube', label: '標準', count: facets?.license.youtube ?? 0 },
            { value: 'creative_commons', label: 'CC', count: facets?.license.creative_commons ?? 0 },
          ]}
        />
      </section>
    </div>
  )
}

function ActiveChips({ ctl, playlistTitle }: { ctl: ReturnType<typeof useMediaFilters>; playlistTitle?: string }) {
  const { f, set } = ctl
  const chips: { k: string; label: ReactNode; clear: () => void }[] = []
  if (f.playlist) chips.push({ k: 'pl', label: `播放清單：${playlistTitle ?? '…'}`, clear: () => set({ playlist: null }) })
  if (f.cap !== 'all') chips.push({ k: 'cap', label: { manual: '有原生字幕', none: '無字幕', unknown: '尚未檢查字幕' }[f.cap], clear: () => set({ cap: null }) })
  if (f.auto) chips.push({ k: 'auto', label: '含自動字幕', clear: () => set({ auto: null }) })
  if (f.status.length) chips.push({ k: 'st', label: `狀態：${f.status.map((s) => mediaStatus[s].label).join('、')}`, clear: () => set({ status: null }) })
  if (f.minDur !== null || f.maxDur !== null)
    chips.push({ k: 'dur', label: `長度 ${f.minDur ?? 0}–${f.maxDur ?? '∞'} 分`, clear: () => set({ min: null, max: null }) })
  if (f.after || f.before) chips.push({ k: 'date', label: `${f.after || '…'} ~ ${f.before || '…'}`, clear: () => set({ after: null, before: null }) })
  if (f.license) chips.push({ k: 'lic', label: licenseLabel[f.license], clear: () => set({ license: null }) })
  if (!chips.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <span key={c.k} className="inline-flex h-8 max-w-full items-center gap-1 rounded-full border border-primary/25 bg-primary-soft pl-3 pr-1 text-xs font-medium text-primary-soft-fg">
          <span className="truncate">{c.label}</span>
          <button onClick={c.clear} className="grid size-6 place-items-center rounded-full hover:bg-primary/15" aria-label="移除篩選">
            <X className="size-3" />
          </button>
        </span>
      ))}
      <button onClick={ctl.clear} className="h-8 px-2 text-xs text-fg-3 hover:text-fg">
        清除全部
      </button>
    </div>
  )
}

/* ───────────── download dialog ───────────── */
export function DownloadDialog({
  open,
  onOpenChange,
  count,
  selection,
  query,
  sample,
  onDone,
  manualCount,
}: {
  manualCount?: number
  open: boolean
  onOpenChange: (o: boolean) => void
  count: number
  selection: { ids?: string[]; filter?: MediaQuery }
  query: MediaQuery
  sample: MediaSummary[]
  onDone: () => void
}) {
  const qc = useQueryClient()
  const langs = useMemo(() => {
    const s = new Map<string, number>()
    sample.forEach((m) => m.manual_caption_langs.forEach((l) => s.set(l, (s.get(l) ?? 0) + 1)))
    if (!s.size) s.set('zh-TW', 0)
    return [...s.entries()].sort((a, b) => b[1] - a[1]).map(([l]) => l)
  }, [sample])
  const [picked, setPicked] = useState<string[]>([])
  const [includeAuto, setIncludeAuto] = useState(false)
  useEffect(() => {
    if (open) {
      setPicked(langs.slice(0, 2))
      setIncludeAuto(!!query.include_auto)
    }
  }, [open, langs, query.include_auto])
  const withManual = selection.ids ? sample.filter((m) => selection.ids!.includes(m.id) && m.has_manual_captions).length : (manualCount ?? null)
  const m = useMutation({
    mutationFn: () => api.download({ media_ids: selection.ids, filter: selection.filter, caption_langs: picked, include_auto: includeAuto }),
    onSuccess: (r) => {
      toast.success(`已排入 ${fmtInt(r.queued)} 部影片下載`, { description: r.skipped ? `${fmtInt(r.skipped)} 部因沒有可用字幕或已在處理中而略過` : '處理進度可在工作佇列查看' })
      qc.invalidateQueries({ queryKey: ['media'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      onOpenChange(false)
      onDone()
    },
    onError: (e) => toast.error(errMsg(e)),
  })
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`下載 ${fmtInt(count)} 部影片`}
      description={selection.filter ? '依目前的篩選條件選取（包含未載入到本頁的影片）。' : '下載音訊與指定語言的字幕，完成後自動進入處理流程。'}
      footer={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button variant="primary" loading={m.isPending} onClick={() => m.mutate()} disabled={!picked.length && !includeAuto}>
            <Download /> 開始下載
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className={cn('grid grid-cols-2 gap-2 rounded-xl bg-surface-2/70 p-3 text-center', selection.ids && 'sm:grid-cols-3')}>
          <div>
            <div className="text-lg font-semibold">{fmtInt(count)}</div>
            <div className="text-[11px] text-fg-3">已選影片</div>
          </div>
          <div>
            <div className="text-lg font-semibold">{withManual === null ? '—' : fmtInt(withManual)}</div>
            <div className="text-[11px] text-fg-3">有原生字幕</div>
          </div>
          {selection.ids && (
            <div className="col-span-2 sm:col-span-1">
              <div className="text-lg font-semibold">{fmtHours(sample.filter((x) => selection.ids!.includes(x.id)).reduce((a, x) => a + (x.duration_sec ?? 0), 0))}</div>
              <div className="text-[11px] text-fg-3">預估長度</div>
            </div>
          )}
        </div>
        <Field label="原生字幕語言" hint="只下載這些語言的上傳者字幕；沒有任一語言字幕的影片會被略過。">
          <div className="flex flex-wrap gap-1.5">
            {[...new Set([...langs, 'zh-TW', 'nan', 'zh-Hant', 'en'])].map((l) => (
              <Chip key={l} active={picked.includes(l)} onClick={() => setPicked((p) => (p.includes(l) ? p.filter((x) => x !== l) : [...p, l]))}>
                {lang(l)} <span className="font-mono text-[11px] opacity-70">{l}</span>
              </Chip>
            ))}
          </div>
        </Field>
        <div className={cn('rounded-xl border p-3.5', includeAuto ? 'border-warning/40 bg-warning-soft/60' : 'border-border')}>
          <Switch
            checked={includeAuto}
            onCheckedChange={setIncludeAuto}
            label="允許使用 YouTube 自動字幕"
            description="沒有原生字幕時改用自動語音辨識字幕。這類音段會標記為「自動字幕」，建立資料集時預設排除。"
          />
          {includeAuto && (
            <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warning-fg">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> 自動字幕對台語的辨識錯誤率高，需要大量人工校正。
            </p>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/* ───────────── table pieces ───────────── */
function TitleCell({ m, short }: { m: MediaSummary; short?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <Thumb src={m.thumbnail_url} seed={m.id} className={cn(short ? 'h-12 w-[27px]' : 'h-[38px] w-[68px]')}>
        {!short && (
          <span className="absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[10px] font-medium leading-4 text-white tabular">{fmtClock(m.duration_sec)}</span>
        )}
      </Thumb>
      <div className="min-w-0">
        <div className="line-clamp-2 text-[13px] font-medium leading-5 text-fg break-cjk" title={m.title}>
          {m.title}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5">
          {m.license === 'creative_commons' && <Tag className="border-info/30 text-info-fg">CC 授權</Tag>}
          {m.kind === 'short' && !short && <Tag>Shorts</Tag>}
        </div>
      </div>
    </div>
  )
}

function QualityCell({ m }: { m: MediaSummary }) {
  if (!m.quality) return <span className="text-xs text-fg-3">—</span>
  return (
    <div className="text-xs leading-5 tabular">
      <div className="text-fg">{fmtDb(m.quality.snr_mean)}</div>
      <div className="text-fg-3">MOS {fmtMos(m.quality.dnsmos_ovrl)}</div>
    </div>
  )
}

/* ───────────── main ───────────── */
export function MediaBrowser({ channelId, kind }: { channelId: string; kind: MediaKind[] }) {
  const ctl = useMediaFilters(kind)
  const { f, set } = ctl
  const [qDraft, setQDraft] = useState(f.q)
  useEffect(() => {
    const t = setTimeout(() => qDraft !== f.q && set({ q: qDraft }), 300)
    return () => clearTimeout(t)
  }, [qDraft]) // eslint-disable-line react-hooks/exhaustive-deps
  const query = ctl.query(channelId)
  const res = useMedia({ ...query, limit: LIMIT, offset: f.offset })
  const playlists = usePlaylists(channelId)
  const plTitle = playlists.data?.items.find((p) => p.id === f.playlist)?.title
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [allMatching, setAllMatching] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [dlOpen, setDlOpen] = useState(false)
  const isDesktop = useIsDesktop()
  const navigate = useNavigate()
  const short = kind.length === 1 && kind[0] === 'short'
  const rows = res.data?.items ?? []
  const total = res.data?.total ?? 0

  // a filter change invalidates "all matching"
  const qKey = JSON.stringify(query)
  useEffect(() => {
    setAllMatching(false)
    setSelected(new Set())
  }, [qKey])

  const toggle = (id: string) => {
    setAllMatching(false)
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }
  const togglePage = (on: boolean) => {
    setAllMatching(false)
    setSelected((s) => {
      const n = new Set(s)
      rows.forEach((r) => (on ? n.add(r.id) : n.delete(r.id)))
      return n
    })
  }
  const selCount = allMatching ? total : selected.size
  const pageAllSelected = rows.length > 0 && rows.every((r) => selected.has(r.id))

  const columns: Column<MediaSummary>[] = [
    { id: 'title', header: '標題', cell: (m) => <TitleCell m={m} short={short} /> },
    { id: 'dur', header: '長度', width: 'w-20', align: 'right', cell: (m) => <span className="text-fg-2">{fmtClock(m.duration_sec)}</span> },
    { id: 'pub', header: '發布', width: 'w-28', hideBelow: 'lg', cell: (m) => <span className="tabular text-fg-2">{fmtDate(m.published_at)}</span> },
    { id: 'views', header: '觀看', width: 'w-20', align: 'right', hideBelow: 'xl', cell: (m) => <span className="text-fg-2">{fmtCompact(m.view_count)}</span> },
    {
      id: 'cap',
      header: '字幕',
      width: 'w-36',
      cell: (m) => <CaptionBadges manual={m.has_manual_captions} auto={m.has_auto_captions} manualLangs={m.manual_caption_langs} autoLangs={m.auto_caption_langs} />,
    },
    { id: 'q', header: '音質', width: 'w-24', hideBelow: 'xl', cell: (m) => <QualityCell m={m} /> },
    { id: 'status', header: '狀態', width: 'w-28', cell: (m) => <MediaStatusBadge status={m.status} stage={m.stage} /> },
  ]

  const card = (m: MediaSummary) => (
    <div className="min-w-0">
      <TitleCell m={m} short={short} />
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-0 text-xs text-fg-3">
        <MediaStatusBadge status={m.status} stage={m.stage} />
        <CaptionBadges compact manual={m.has_manual_captions} auto={m.has_auto_captions} manualLangs={m.manual_caption_langs} autoLangs={m.auto_caption_langs} />
        <span className="tabular">{fmtDate(m.published_at)}</span>
        {m.quality && <span className="tabular">SNR {fmtDb(m.quality.snr_mean)}</span>}
      </div>
    </div>
  )

  const filterPanel = <FilterPanel ctl={ctl} facets={res.data?.facets} />

  return (
    <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
      {isDesktop && (
        <aside className="self-start xl:sticky xl:top-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold">篩選</h3>
            {ctl.activeCount > 0 && (
              <button onClick={ctl.clear} className="text-xs text-fg-3 hover:text-fg">
                重設
              </button>
            )}
          </div>
          {filterPanel}
        </aside>
      )}

      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput value={qDraft} onChange={setQDraft} placeholder="搜尋標題或說明" className="min-w-0 flex-1 basis-full sm:basis-56" />
          {!isDesktop && (
            <Button onClick={() => setFiltersOpen(true)} className="flex-1 sm:flex-none">
              <ListFilter /> 篩選
              {ctl.activeCount > 0 && <span className="grid size-5 place-items-center rounded-full bg-primary text-[11px] text-primary-fg">{ctl.activeCount}</span>}
            </Button>
          )}
          <Select value={f.sort} onChange={(e) => set({ sort: e.target.value })} className="flex-1 sm:w-40 sm:flex-none" aria-label="排序">
            <option value="-published_at">最新發布</option>
            <option value="published_at">最早發布</option>
            <option value="-duration">最長</option>
            <option value="duration">最短</option>
            <option value="-views">最多觀看</option>
            <option value="title">標題</option>
          </Select>
        </div>
        <ActiveChips ctl={ctl} playlistTitle={plTitle} />

        {/* selection / summary bar */}
        <div className={cn('flex min-h-11 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3 py-1.5 text-[13px]', selCount ? 'bg-primary-soft text-primary-soft-fg' : 'bg-surface-2/70 text-fg-2')}>
          <span data-shot="select-all-page" className="inline-grid md:hidden">
            <Checkbox
              aria-label="選取本頁全部"
              checked={allMatching || pageAllSelected ? true : selected.size ? 'indeterminate' : false}
              onCheckedChange={(v) => togglePage(v)}
            />
          </span>
          {selCount ? (
            <>
              <span className="font-medium">
                已選 {fmtInt(selCount)} 部{allMatching ? '（全部符合條件）' : ''}
              </span>
              {!allMatching && pageAllSelected && total > rows.length && (
                <button data-shot="select-all-matching" className="font-medium underline underline-offset-2 hover:no-underline" onClick={() => setAllMatching(true)}>
                  選取全部符合的 {fmtInt(total)} 部
                </button>
              )}
              <span className="flex-1" />
              <Button size="sm" variant="ghost" onClick={() => (setSelected(new Set()), setAllMatching(false))}>
                清除
              </Button>
              <Button size="sm" variant="primary" data-shot="open-download" onClick={() => setDlOpen(true)}>
                <Download /> 下載…
              </Button>
            </>
          ) : (
            <span className="tabular">
              {res.isFetching && !res.data ? '載入中…' : `符合 ${fmtInt(total)} 部`}
              {res.data && !f.auto && res.data.facets.captions.auto_only > 0 && f.cap !== 'unknown' && (
                <span className="text-fg-3">　·　另有 {fmtInt(res.data.facets.captions.auto_only)} 部僅有自動字幕（已隱藏）</span>
              )}
            </span>
          )}
        </div>

        <Card className="overflow-clip">
          {res.isError ? (
            <ErrorState error={res.error} onRetry={() => res.refetch()} />
          ) : !res.data ? (
            <ListSkeleton rows={8} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={short ? <Smartphone /> : <Film />}
              title="沒有符合條件的影片"
              description={ctl.activeCount ? '調整或清除篩選條件試試看。' : '這個頻道目前沒有已列出的影片，可以重新列出頻道。'}
              action={ctl.activeCount ? <Button onClick={ctl.clear}>清除篩選</Button> : undefined}
            />
          ) : (
            <div className={cn('transition-opacity', res.isFetching && 'opacity-60')}>
              <DataTable
                rows={rows}
                columns={columns}
                card={card}
                selected={allMatching ? new Set(rows.map((r) => r.id)) : selected}
                onToggle={toggle}
                onToggleAll={togglePage}
                onRowClick={(m) => navigate(`/media/${m.id}`)}
              />
              <Pagination total={total} limit={LIMIT} offset={f.offset} onChange={(o) => set({ offset: o }, true)} className="border-t border-border" />
            </div>
          )}
        </Card>
      </div>

      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen} title="篩選影片" description={`符合 ${fmtInt(total)} 部`}>
        <div className="p-4">{filterPanel}</div>
        <div className="sticky bottom-0 flex gap-2 border-t border-border bg-surface p-3">
          <Button className="flex-1" onClick={ctl.clear}>
            重設
          </Button>
          <Button className="flex-1" variant="primary" onClick={() => setFiltersOpen(false)}>
            顯示 {fmtInt(total)} 部
          </Button>
        </div>
      </Sheet>

      <DownloadDialog
        open={dlOpen}
        onOpenChange={setDlOpen}
        count={selCount}
        selection={allMatching ? { filter: query } : { ids: [...selected] }}
        query={query}
        sample={rows}
        manualCount={res.data?.facets.captions.manual}
        onDone={() => {
          setSelected(new Set())
          setAllMatching(false)
        }}
      />
    </div>
  )
}
