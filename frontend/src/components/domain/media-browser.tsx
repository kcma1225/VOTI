import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Captions, Download, Film, Info, ListFilter, Smartphone, X } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, useMedia, usePlaylists } from '@/api/queries'
import type { CaptionFilter, MediaKind, MediaQuery, MediaSort, MediaStatus, MediaSummary } from '@/api/types'
import { fmtClock, fmtCompact, fmtDate, fmtDb, fmtHours, fmtInt, fmtMos } from '@/lib/format'
import { lang, licenseLabel, mediaStatus } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useIsDesktop } from '@/hooks/useMedia'
import { Button } from '@/components/ui/button'
import { Badge, Card, Tag, Thumb } from '@/components/ui/primitives'
import { Checkbox, Chip, Field, Input, NumberInput, SearchInput, Segmented, Select, Switch } from '@/components/ui/form'
import { DataTable, Pagination, type Column } from '@/components/ui/data-table'
import { Dialog, Sheet, Tooltip } from '@/components/ui/overlay'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { CaptionBadges, MediaStatusBadge } from './status'
import { useT } from '@/i18n'

const LIMIT = 50
type CapUI = 'all' | 'manual' | 'none' | 'unknown'
type DlUI = '' | 'yes' | 'no'

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
    dl: (get('dl') === 'yes' || get('dl') === 'no' ? get('dl') : '') as DlUI,
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
    downloaded: f.dl === 'yes' ? true : f.dl === 'no' ? false : undefined,
    sort: f.sort,
  })
  const activeCount =
    (f.cap !== 'all' ? 1 : 0) + (f.auto ? 1 : 0) + (f.status.length ? 1 : 0) + (f.minDur !== null || f.maxDur !== null ? 1 : 0) + (f.after || f.before ? 1 : 0) + (f.license ? 1 : 0) + (f.dl ? 1 : 0)
  return { f, set, query, activeCount, clear: () => setSp(new URLSearchParams(), { replace: true }) }
}

/* ───────────── filter panel ───────────── */
function RadioRows<V extends string>({ label, value, onChange, options }: { label: string; value: V; onChange: (v: V) => void; options: [V, string, number | undefined][] }) {
  return (
    <div className="space-y-1" role="radiogroup" aria-label={label}>
      {options.map(([v, text, n]) => (
        <button
          key={v}
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cn(
            'flex h-10 w-full items-center justify-between gap-2 rounded-lg px-3 text-left text-[13px] transition-colors sm:h-9',
            value === v ? 'bg-primary-soft font-medium text-primary-soft-fg' : 'text-fg-2 hover:bg-surface-2',
          )}
        >
          <span className="flex min-w-0 items-center gap-2.5">
            <span className={cn('grid size-4 shrink-0 place-items-center rounded-full border', value === v ? 'border-primary' : 'border-border-strong')}>
              {value === v && <span className="size-2 rounded-full bg-primary" />}
            </span>
            <span className="truncate">{text}</span>
          </span>
          {n !== undefined && <span className="tabular text-xs text-fg-3">{fmtInt(n)}</span>}
        </button>
      ))}
    </div>
  )
}

function FilterPanel({ ctl, facets, captionsPending }: { ctl: ReturnType<typeof useMediaFilters>; facets?: import('@/api/types').MediaFacets; captionsPending?: boolean }) {
  const { f, set } = ctl
  const t = useT()
  const fc = facets?.captions
  const durPresets: [string, number | null, number | null][] = [
    [t('collect.media.durUnder1'), null, 1],
    [t('collect.media.dur1to10'), 1, 10],
    [t('collect.media.dur10to30'), 10, 30],
    [t('collect.media.dur30plus'), 30, null],
  ]
  const statuses: MediaStatus[] = ['listed', 'detailed', 'queued', 'downloading', 'processing', 'ready', 'failed']
  return (
    <div className="space-y-6">
      <section>
        <h4 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold">
          <Captions className="size-4 text-fg-3" /> {t('collect.media.capHeading')}
        </h4>
        {captionsPending && (
          <p className="mb-2 flex items-start gap-1.5 rounded-lg bg-info-soft px-2.5 py-2 text-xs leading-5 text-info-fg">
            <Info className="mt-0.5 size-3.5 shrink-0" />
            {t('collect.media.capIncomplete')}
          </p>
        )}
        <RadioRows
          label={t('collect.media.capHeading')}
          value={f.cap}
          onChange={(v) => set({ cap: v === 'all' ? null : v })}
          options={[
            ['all', t('collect.media.capAll'), undefined],
            ['manual', t('collect.media.capManual'), fc?.manual],
            ['none', t('collect.media.capNone'), fc?.none],
            ['unknown', t('collect.media.capUnknown'), fc?.unknown],
          ]}
        />
        <div className="mt-3 rounded-lg border border-border bg-surface-2/50 p-3">
          <Switch
            checked={f.auto}
            onCheckedChange={(v) => set({ auto: v })}
            label={t('collect.media.includeAuto')}
            description={t('collect.media.includeAutoDesc', fc ? fc.auto_only : null)}
          />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">{t('collect.media.dlHeading')}</h4>
        <RadioRows
          label={t('collect.media.dlHeading')}
          value={f.dl || 'all'}
          onChange={(v) => set({ dl: v === 'all' ? null : v })}
          options={[
            ['all', t('collect.media.dlAll'), undefined],
            ['yes', t('collect.media.dlYes'), facets?.downloaded?.yes],
            ['no', t('collect.media.dlNo'), facets?.downloaded?.no],
          ]}
        />
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">{t('collect.media.statusHeading')}</h4>
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
        <h4 className="mb-2 text-[13px] font-semibold">{t('collect.media.durationHeading')}</h4>
        <div className="mb-2 flex flex-wrap gap-1.5">
          {durPresets.map(([label, a, b]) => (
            <Chip key={label} active={f.minDur === a && f.maxDur === b} onClick={() => (f.minDur === a && f.maxDur === b ? set({ min: null, max: null }) : set({ min: a, max: b }))}>
              {label}
            </Chip>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <NumberInput aria-label={t('collect.media.minDur')} value={f.minDur} onChange={(v) => set({ min: v })} min={0} suffix={t('collect.media.minSuffix')} />
          <NumberInput aria-label={t('collect.media.maxDur')} value={f.maxDur} onChange={(v) => set({ max: v })} min={0} suffix={t('collect.media.maxSuffix')} />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">{t('collect.media.dateHeading')}</h4>
        <div className="grid grid-cols-2 gap-2">
          <Input type="date" aria-label={t('collect.media.dateFrom')} value={f.after} onChange={(e) => set({ after: e.target.value })} />
          <Input type="date" aria-label={t('collect.media.dateTo')} value={f.before} onChange={(e) => set({ before: e.target.value })} />
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-[13px] font-semibold">{t('collect.media.licenseHeading')}</h4>
        <Segmented
          className="w-full [&>*]:flex-1"
          value={f.license || 'all'}
          onChange={(v) => set({ license: v === 'all' ? null : v })}
          options={[
            { value: 'all', label: t('collect.media.licAll') },
            { value: 'youtube', label: t('collect.media.licStandard'), count: facets?.license.youtube ?? 0 },
            { value: 'creative_commons', label: t('collect.media.licCc'), count: facets?.license.creative_commons ?? 0 },
          ]}
        />
      </section>
    </div>
  )
}

function ActiveChips({ ctl, playlistTitle }: { ctl: ReturnType<typeof useMediaFilters>; playlistTitle?: string }) {
  const { f, set } = ctl
  const t = useT()
  const chips: { k: string; label: ReactNode; clear: () => void }[] = []
  if (f.playlist) chips.push({ k: 'pl', label: t('collect.media.chipPlaylist', playlistTitle ?? '…'), clear: () => set({ playlist: null }) })
  if (f.cap !== 'all') chips.push({ k: 'cap', label: { manual: t('collect.media.chipManual'), none: t('collect.media.chipNone'), unknown: t('collect.media.chipUnknown') }[f.cap], clear: () => set({ cap: null }) })
  if (f.auto) chips.push({ k: 'auto', label: t('collect.media.chipAuto'), clear: () => set({ auto: null }) })
  if (f.status.length) chips.push({ k: 'st', label: t('collect.media.chipStatus', f.status.map((s) => mediaStatus[s].label).join(t('common.listSep'))), clear: () => set({ status: null }) })
  if (f.minDur !== null || f.maxDur !== null)
    chips.push({ k: 'dur', label: t('collect.media.chipDuration', String(f.minDur ?? 0), String(f.maxDur ?? '∞')), clear: () => set({ min: null, max: null }) })
  if (f.after || f.before) chips.push({ k: 'date', label: `${f.after || '…'} ~ ${f.before || '…'}`, clear: () => set({ after: null, before: null }) })
  if (f.license) chips.push({ k: 'lic', label: licenseLabel[f.license], clear: () => set({ license: null }) })
  if (f.dl) chips.push({ k: 'dl', label: f.dl === 'yes' ? t('collect.media.chipDlYes') : t('collect.media.chipDlNo'), clear: () => set({ dl: null }) })
  if (!chips.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <span key={c.k} className="inline-flex h-8 max-w-full items-center gap-1 rounded-full border border-primary/25 bg-primary-soft pl-3 pr-1 text-xs font-medium text-primary-soft-fg">
          <span className="truncate">{c.label}</span>
          <button onClick={c.clear} className="grid size-6 place-items-center rounded-full hover:bg-primary/15" aria-label={t('collect.media.removeFilter')}>
            <X className="size-3" />
          </button>
        </span>
      ))}
      <button onClick={ctl.clear} className="h-8 px-2 text-xs text-fg-3 hover:text-fg">
        {t('collect.media.clearAll')}
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
  const t = useT()
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
      toast.success(t('collect.media.dl.toast', r.queued), { description: r.skipped ? t('collect.media.dl.toastSkipped', r.skipped) : t('collect.media.dl.toastHint') })
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
      title={t('collect.media.dl.title', count)}
      description={selection.filter ? t('collect.media.dl.descFilter') : t('collect.media.dl.descIds')}
      footer={
        <>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button variant="primary" loading={m.isPending} onClick={() => m.mutate()} disabled={!picked.length && !includeAuto}>
            <Download /> {t('collect.media.dl.start')}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className={cn('grid grid-cols-2 gap-2 rounded-xl bg-surface-2/70 p-3 text-center', selection.ids && 'sm:grid-cols-3')}>
          <div>
            <div className="text-lg font-semibold">{fmtInt(count)}</div>
            <div className="text-[11px] text-fg-3">{t('collect.media.dl.selected')}</div>
          </div>
          <div>
            <div className="text-lg font-semibold">{withManual === null ? '—' : fmtInt(withManual)}</div>
            <div className="text-[11px] text-fg-3">{t('collect.media.dl.withManual')}</div>
          </div>
          {selection.ids && (
            <div className="col-span-2 sm:col-span-1">
              <div className="text-lg font-semibold">{fmtHours(sample.filter((x) => selection.ids!.includes(x.id)).reduce((a, x) => a + (x.duration_sec ?? 0), 0))}</div>
              <div className="text-[11px] text-fg-3">{t('collect.media.dl.estLength')}</div>
            </div>
          )}
        </div>
        <Field label={t('collect.media.dl.langs')} hint={t('collect.media.dl.langsHint')}>
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
            label={t('collect.media.dl.allowAuto')}
            description={t('collect.media.dl.allowAutoDesc')}
          />
          {includeAuto && (
            <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-warning-fg">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> {t('collect.media.dl.autoWarn')}
            </p>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/* ───────────── table pieces ───────────── */
function TitleCell({ m, short }: { m: MediaSummary; short?: boolean }) {
  const t = useT()
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
        <div className="mt-0.5 flex items-center gap-1.5 empty:hidden">
          {m.is_new && (
            <Tooltip content={t('collect.media.newTip')}>
              <Badge tone="primary" className="h-[18px] px-1.5 text-[11px]">
                {t('collect.media.newBadge')}
              </Badge>
            </Tooltip>
          )}
          {m.license === 'creative_commons' && <Tag className="border-info/30 text-info-fg">{t('collect.media.ccLicense')}</Tag>}
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
export function MediaBrowser({ channelId, kind, captionsPending }: { channelId: string; kind: MediaKind[]; captionsPending?: boolean }) {
  const ctl = useMediaFilters(kind)
  const { f, set } = ctl
  const t = useT()
  const [qDraft, setQDraft] = useState(f.q)
  useEffect(() => {
    const timer = setTimeout(() => qDraft !== f.q && set({ q: qDraft }), 300)
    return () => clearTimeout(timer)
  }, [qDraft]) // eslint-disable-line react-hooks/exhaustive-deps
  const query = ctl.query(channelId)
  // while caption details arrive in the background, refresh the list (and facet counts) now and then
  const res = useMedia({ ...query, limit: LIMIT, offset: f.offset }, true, captionsPending ? 10_000 : false)
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
    { id: 'title', header: t('collect.media.cols.title'), cell: (m) => <TitleCell m={m} short={short} /> },
    { id: 'dur', header: t('collect.media.cols.duration'), width: 'w-20', align: 'right', cell: (m) => <span className="text-fg-2">{fmtClock(m.duration_sec)}</span> },
    { id: 'pub', header: t('collect.media.cols.published'), width: 'w-28', hideBelow: 'lg', cell: (m) => <span className="tabular text-fg-2">{fmtDate(m.published_at)}</span> },
    { id: 'views', header: t('collect.media.cols.views'), width: 'w-20', align: 'right', hideBelow: 'xl', cell: (m) => <span className="text-fg-2">{fmtCompact(m.view_count)}</span> },
    {
      id: 'cap',
      header: t('collect.media.cols.captions'),
      width: 'w-36',
      cell: (m) => <CaptionBadges manual={m.has_manual_captions} auto={m.has_auto_captions} manualLangs={m.manual_caption_langs} autoLangs={m.auto_caption_langs} />,
    },
    { id: 'q', header: t('collect.media.cols.quality'), width: 'w-24', hideBelow: 'xl', cell: (m) => <QualityCell m={m} /> },
    { id: 'status', header: t('collect.media.cols.status'), width: 'w-28', cell: (m) => <MediaStatusBadge status={m.status} stage={m.stage} /> },
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

  const filterPanel = <FilterPanel ctl={ctl} facets={res.data?.facets} captionsPending={captionsPending} />

  return (
    <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
      {isDesktop && (
        <aside className="self-start xl:sticky xl:top-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-sm font-semibold">{t('collect.media.filters')}</h3>
            {ctl.activeCount > 0 && (
              <button onClick={ctl.clear} className="text-xs text-fg-3 hover:text-fg">
                {t('collect.media.reset')}
              </button>
            )}
          </div>
          {filterPanel}
        </aside>
      )}

      <div className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput value={qDraft} onChange={setQDraft} placeholder={t('collect.media.searchPlaceholder')} className="min-w-0 flex-1 basis-full sm:basis-56" />
          {!isDesktop && (
            <Button onClick={() => setFiltersOpen(true)} className="flex-1 sm:flex-none">
              <ListFilter /> {t('collect.media.filters')}
              {ctl.activeCount > 0 && <span className="grid size-5 place-items-center rounded-full bg-primary text-[11px] text-primary-fg">{ctl.activeCount}</span>}
            </Button>
          )}
          <Select value={f.sort} onChange={(e) => set({ sort: e.target.value })} className="flex-1 sm:w-40 sm:flex-none" aria-label={t('collect.media.sort')}>
            <option value="-published_at">{t('collect.media.sortNewest')}</option>
            <option value="published_at">{t('collect.media.sortOldest')}</option>
            <option value="-duration">{t('collect.media.sortLongest')}</option>
            <option value="duration">{t('collect.media.sortShortest')}</option>
            <option value="-views">{t('collect.media.sortViews')}</option>
            <option value="title">{t('collect.media.sortTitle')}</option>
          </Select>
        </div>
        <ActiveChips ctl={ctl} playlistTitle={plTitle} />

        {/* selection / summary bar */}
        <div className={cn('flex min-h-11 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3 py-1.5 text-[13px]', selCount ? 'bg-primary-soft text-primary-soft-fg' : 'bg-surface-2/70 text-fg-2')}>
          <span data-shot="select-all-page" className="inline-grid md:hidden">
            <Checkbox
              aria-label={t('collect.media.selectPage')}
              checked={allMatching || pageAllSelected ? true : selected.size ? 'indeterminate' : false}
              onCheckedChange={(v) => togglePage(v)}
            />
          </span>
          {selCount ? (
            <>
              <span className="font-medium">
                {t('collect.media.selected', selCount)}{allMatching ? t('collect.media.allMatchingSuffix') : ''}
              </span>
              {!allMatching && pageAllSelected && total > rows.length && (
                <button data-shot="select-all-matching" className="font-medium underline underline-offset-2 hover:no-underline" onClick={() => setAllMatching(true)}>
                  {t('collect.media.selectAllMatching', total)}
                </button>
              )}
              <span className="flex-1" />
              <Button size="sm" variant="ghost" onClick={() => (setSelected(new Set()), setAllMatching(false))}>
                {t('collect.media.clear')}
              </Button>
              <Button size="sm" variant="primary" data-shot="open-download" onClick={() => setDlOpen(true)}>
                <Download /> {t('collect.media.download')}
              </Button>
            </>
          ) : (
            <span className="tabular">
              {res.isFetching && !res.data ? t('collect.media.loading') : t('collect.media.matching', total)}
              {res.data && !f.auto && res.data.facets.captions.auto_only > 0 && f.cap !== 'unknown' && (
                <span className="text-fg-3">{t('collect.media.autoHidden', res.data.facets.captions.auto_only)}</span>
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
              title={t('collect.media.emptyTitle')}
              description={ctl.activeCount ? t('collect.media.emptyFiltered') : t('collect.media.emptyNone')}
              action={ctl.activeCount ? <Button onClick={ctl.clear}>{t('collect.media.clearFilters')}</Button> : undefined}
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

      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen} title={t('collect.media.sheetTitle')} description={t('collect.media.matching', total)}>
        <div className="p-4">{filterPanel}</div>
        <div className="sticky bottom-0 flex gap-2 border-t border-border bg-surface p-3">
          <Button className="flex-1" onClick={ctl.clear}>
            {t('collect.media.reset')}
          </Button>
          <Button className="flex-1" variant="primary" onClick={() => setFiltersOpen(false)}>
            {t('collect.media.show', total)}
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
