import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Captions,
  ExternalLink,
  Film,
  Gauge,
  ListVideo,
  Loader2,
  MoreHorizontal,
  NotebookPen,
  RefreshCw,
  Repeat2,
  ScanSearch,
  Smartphone,
  Trash2,
  TriangleAlert,
  Users,
} from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, onErr, qk, useMe, useChannel, useDiversity, usePlaylists, useRepeated, useSpeakers } from '@/api/queries'
import type { ChannelDetail as TChannel } from '@/api/types'
import { fmt1, fmtBytes, fmtClock, fmtCompact, fmtHours, fmtInt, fmtMos, fmtRelative, fmtSec, hoursUnit } from '@/lib/format'
import { ageGroupLabel, ageGroupOrder, repeatPosition, sexLabel } from '@/lib/labels'
import { Page } from '@/components/layout/page'
import { useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { NavTabs } from '@/components/ui/tabs'
import { Dialog, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from '@/components/ui/overlay'
import { Field, Input, Switch, Textarea } from '@/components/ui/form'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { MediaBrowser } from '@/components/domain/media-browser'
import { QualityPanels } from '@/components/domain/quality-viz'
import { SpeakerList } from '@/components/domain/speakers'
import { HBars, ORDINAL, StackBar } from '@/components/charts/charts'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'

function DeleteChannelDialog({ c, open, onOpenChange }: { c: TChannel; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [typed, setTyped] = useState('')
  const t = useT()
  const impact = useQuery({ queryKey: ['channel-delete-impact', c.id], queryFn: () => api.channelDeleteImpact(c.id), enabled: open, staleTime: 0 })
  const del = useMutation({
    mutationFn: () => api.deleteChannel(c.id, impact.data?.requires_confirmation ? typed : undefined),
    onSuccess: () => {
      toast.success(t('collect.channel.del.toastDone', c.title))
      onOpenChange(false)
      qc.removeQueries({ queryKey: qk.channel(c.id) })
      qc.invalidateQueries()
      navigate('/channels', { replace: true })
    },
    onError: onErr,
  })
  const d = impact.data
  const mustType = !!d?.requires_confirmation
  const canDelete = !!d && !del.isPending && (!mustType || typed.trim() === c.title.trim())
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o)
        if (o) setTyped('')
      }}
      title={t('collect.channel.del.title')}
      description={t('collect.channel.del.description', <span className="font-medium text-fg">{c.title}</span>)}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button variant="danger" loading={del.isPending} disabled={!canDelete} onClick={() => del.mutate()}>
            <Trash2 /> {t('collect.channel.del.submit')}
          </Button>
        </>
      }
    >
      {impact.isLoading || !d ? (
        impact.isError ? <p className="text-[13px] text-danger-fg">{errMsg(impact.error)}</p> : <Skeleton className="h-24" />
      ) : (
        <div className="space-y-4">
          {mustType ? (
            <div className="flex gap-3 rounded-lg border border-danger/30 bg-danger-soft px-3.5 py-3 text-[13px] leading-5">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger-fg" />
              <div className="min-w-0 space-y-1.5">
                <p className="font-medium text-danger-fg">{t('collect.channel.del.warnHead')}</p>
                <ul className="list-disc space-y-0.5 pl-4 text-fg-2">
                  <li>{t('collect.channel.del.audio', d.downloaded_media, fmtHours(d.downloaded_sec), fmtBytes(d.storage_bytes))}</li>
                  <li>{t('collect.channel.del.analyses')}</li>
                  <li>{t('collect.channel.del.segments', d.segments)}{d.reviewed_segments > 0 && <>{t('collect.channel.del.reviewedPart')}<span className="font-medium text-danger-fg">{t('collect.channel.del.reviewed', d.reviewed_segments)}</span></>}</li>
                  {d.active_jobs > 0 && <li>{t('collect.channel.del.activeJobs', d.active_jobs)}</li>}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-[13px] leading-5 text-fg-2">{t('collect.channel.del.metadataOnly', d.media_total, d.playlists)}</p>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-lg bg-surface-2 px-3.5 py-3 text-[13px] sm:grid-cols-4">
            {[
              [t('collect.channel.del.statVideos'), fmtInt(d.media_total)],
              [t('collect.channel.del.statDownloaded'), fmtInt(d.downloaded_media)],
              [t('collect.channel.del.statSegments'), fmtInt(d.segments)],
              [t('collect.channel.del.statStorage'), fmtBytes(d.storage_bytes)],
            ].map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-fg-3">{k}</dt>
                <dd className="tabular font-medium">{v}</dd>
              </div>
            ))}
          </dl>
          {d.datasets_referencing > 0 && (
            <p className="text-[12px] leading-5 text-fg-3">{t('collect.channel.del.datasets', d.datasets_referencing)}</p>
          )}
          {mustType && (
            <Field label={t('collect.channel.del.typeToConfirm', <span className="font-medium text-fg">{c.title}</span>)} htmlFor="confirm-channel-title">
              <Input id="confirm-channel-title" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" placeholder={c.title} invalid={typed.length > 0 && typed.trim() !== c.title.trim()} />
            </Field>
          )}
        </div>
      )}
    </Dialog>
  )
}

function Header({ c }: { c: TChannel }) {
  const qc = useQueryClient()
  const isAdmin = useMe().data?.role === 'admin'
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [notesOpen, setNotesOpen] = useState(false)
  const [notes, setNotes] = useState(c.notes)
  const t = useT()
  const refresh = useMutation({
    mutationFn: () => api.refreshChannel(c.id),
    onSuccess: () => {
      toast.success(t('collect.channel.toastRelist'))
      qc.invalidateQueries({ queryKey: qk.channel(c.id) })
    },
    onError: onErr,
  })
  const detail = useMutation({
    mutationFn: () => api.detailChannel(c.id),
    onSuccess: (r) => toast.success(r.queued ? t('collect.channel.toastDetailQueued', r.queued) : t('collect.channel.toastDetailAll')),
    onError: onErr,
  })
  const dedup = useMutation({ mutationFn: () => api.dedup(c.id), onSuccess: () => toast.success(t('collect.channel.toastDedup')), onError: onErr })
  const saveNotes = useMutation({
    mutationFn: () => api.patchChannel(c.id, notes),
    onSuccess: (d) => {
      qc.setQueryData(qk.channel(c.id), d)
      setNotesOpen(false)
      toast.success(t('collect.channel.toastNotesSaved'))
    },
    onError: (e) => toast.error(errMsg(e)),
  })
  const stats = [
    { label: t('collect.channel.stats.videos'), value: fmtInt(c.kind_counts?.video ?? c.counts.listed), hint: c.video_count && c.video_count > c.counts.listed ? t('collect.channel.stats.ytShows', c.video_count) : undefined },
    { label: t('collect.channel.stats.shorts'), value: fmtInt(c.kind_counts?.short ?? 0) },
    { label: t('collect.channel.stats.manual'), value: fmtInt(c.counts.with_manual_captions), hint: t('collect.channel.stats.checked', c.counts.detailed) },
    { label: t('collect.channel.stats.downloaded'), value: fmtInt(c.counts.downloaded), hint: c.counts.failed ? t('collect.channel.stats.failed', c.counts.failed) : undefined, warn: !!c.counts.failed },
    { label: t('collect.channel.stats.speech'), value: `${fmt1(c.hours.speech)}`, unit: hoursUnit(), hint: t('collect.channel.stats.totalLength', fmt1(c.hours.listed)) },
    { label: t('collect.channel.stats.candidates'), value: fmtInt(c.speakers.candidates), hint: fmtHours(c.speakers.total_sec) },
    { label: t('collect.channel.stats.snr'), value: c.quality.snr_mean != null ? fmt1(c.quality.snr_mean) : '—', unit: 'dB', hint: `DNSMOS ${fmtMos(c.quality.dnsmos_ovrl)}` },
  ]
  return (
    <>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <Avatar name={c.title} src={c.thumbnail_url} size={64} className="hidden sm:grid" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <Avatar name={c.title} src={c.thumbnail_url} size={44} className="sm:hidden" />
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
                <h1 className="truncate text-[22px] font-semibold leading-8 tracking-tight sm:text-2xl">{c.title}</h1>
                {c.list_status === 'listing' && (
                  <Badge tone="primary">
                    <Loader2 className="size-3 animate-spin" /> {t('collect.channel.listingNow')}
                  </Badge>
                )}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-fg-3">
                <span>{c.handle}</span>
                <span>{t('collect.channel.subscribers', fmtCompact(c.subscriber_count))}</span>
                <span>{t('collect.channel.updated', fmtRelative(c.last_listed_at))}</span>
                <a href={c.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 hover:text-primary">
                  YouTube <ExternalLink className="size-3" />
                </a>
              </div>
            </div>
          </div>
          {c.notes && (
            <button onClick={() => setNotesOpen(true)} className="mt-3 flex max-w-3xl items-start gap-2 rounded-lg bg-warning-soft/70 px-3 py-2 text-left text-[13px] leading-5 text-fg-2 hover:bg-warning-soft">
              <NotebookPen className="mt-0.5 size-3.5 shrink-0 text-warning-fg" />
              <span className="line-clamp-2 break-cjk">{c.notes}</span>
            </button>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button onClick={() => refresh.mutate()} loading={refresh.isPending} disabled={c.list_status === 'listing'}>
            <RefreshCw /> {t('collect.channel.relist')}
          </Button>
          <Button onClick={() => detail.mutate()} loading={detail.isPending} className="hidden sm:inline-flex">
            <Captions /> {t('collect.channel.checkCaptions')}
          </Button>
          <Menu>
            <MenuTrigger asChild>
              <Button size="icon" aria-label={t('collect.channel.moreActions')}>
                <MoreHorizontal />
              </Button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem icon={<Captions />} onSelect={() => detail.mutate()}>
                {t('collect.channel.checkAllCaptions')}
              </MenuItem>
              <MenuItem icon={<ScanSearch />} onSelect={() => dedup.mutate()}>
                {t('collect.channel.redetectRepeated')}
              </MenuItem>
              <MenuItem icon={<NotebookPen />} onSelect={() => setNotesOpen(true)}>
                {t('collect.channel.editNotes')}
              </MenuItem>
              {isAdmin && (
                <>
                  <MenuSeparator />
                  <MenuItem danger icon={<Trash2 />} onSelect={() => setDeleteOpen(true)}>
                    {t('collect.channel.deleteChannel')}
                  </MenuItem>
                </>
              )}
            </MenuContent>
          </Menu>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap gap-px overflow-hidden rounded-xl border border-border bg-border">
        {stats.map((s) => (
          <div key={s.label} className="min-w-[calc(50%-1px)] flex-1 bg-surface px-4 py-3 sm:min-w-[calc(25%-1px)] xl:min-w-0">
            <div className="truncate text-xs text-fg-3">{s.label}</div>
            <div className="mt-0.5 flex items-baseline gap-1">
              <span className="text-lg font-semibold leading-7">{s.value}</span>
              {s.unit && <span className="text-xs text-fg-3">{s.unit}</span>}
            </div>
            {s.hint && <div className={cn('truncate text-[11px]', s.warn ? 'text-danger-fg' : 'text-fg-3')}>{s.hint}</div>}
          </div>
        ))}
      </div>

      <DeleteChannelDialog c={c} open={deleteOpen} onOpenChange={setDeleteOpen} />
      <Dialog
        open={notesOpen}
        onOpenChange={(o) => {
          setNotesOpen(o)
          if (o) setNotes(c.notes)
        }}
        title={t('collect.channel.notesTitle')}
        description={t('collect.channel.notesDesc')}
        footer={
          <>
            <Button onClick={() => setNotesOpen(false)}>{t('common.cancel')}</Button>
            <Button variant="primary" loading={saveNotes.isPending} onClick={() => saveNotes.mutate()}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={6} placeholder={t('collect.channel.notesPlaceholder')} />
      </Dialog>
    </>
  )
}

function PlaylistsTab({ cid }: { cid: string }) {
  const pl = usePlaylists(cid)
  const t = useT()
  if (pl.isError) return <ErrorState error={pl.error} onRetry={() => pl.refetch()} />
  if (!pl.data) return <ListSkeleton rows={4} />
  if (!pl.data.items.length)
    return (
      <Card>
        <EmptyState icon={<ListVideo />} title={t('collect.channel.playlists.emptyTitle')} description={t('collect.channel.playlists.emptyDesc')} />
      </Card>
    )
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {pl.data.items.map((p, i) => (
        <Link key={p.id} to={`/channels/${cid}?playlist=${p.id}`} className="group flex min-w-0 items-center gap-4 rounded-xl border border-border bg-surface p-4 shadow-xs hover:border-border-strong hover:shadow-md">
          <div className="relative grid h-14 w-20 shrink-0 place-items-center rounded-lg text-white" style={{ background: `linear-gradient(135deg, var(--series-${(i % 3) + 1}), var(--series-7))` }}>
            <ListVideo className="size-5" />
            <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] font-medium">{fmtInt(p.media_count)}</span>
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium group-hover:text-primary">{p.title}</div>
            <div className="mt-0.5 text-xs text-fg-3">
              {t('collect.channel.playlists.meta', p.media_count, fmtRelative(p.last_listed_at))}
            </div>
          </div>
        </Link>
      ))}
    </div>
  )
}

function RepeatedTab({ cid }: { cid: string }) {
  const rep = useRepeated(cid)
  const t = useT()
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: ({ id, excluded }: { id: string; excluded: boolean }) => api.patchRepeated(id, excluded),
    onSuccess: (_d, v) => {
      qc.setQueryData(qk.repeated(cid), (old: typeof rep.data) => old?.map((g) => (g.group_id === v.id ? { ...g, excluded: v.excluded } : g)))
      toast.success(v.excluded ? t('collect.channel.repeated.toastExcluded') : t('collect.channel.repeated.toastIncluded'))
    },
    onError: onErr,
  })
  const dedup = useMutation({ mutationFn: () => api.dedup(cid), onSuccess: () => toast.success(t('collect.channel.repeated.toastRedetect')), onError: onErr })
  if (rep.isError) return <ErrorState error={rep.error} onRetry={() => rep.refetch()} />
  if (!rep.data) return <ListSkeleton rows={3} />
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2/50 p-4 sm:flex-row sm:items-center">
        <Repeat2 className="hidden size-5 shrink-0 text-fg-3 sm:block" />
        <p className="min-w-0 flex-1 text-[13px] leading-5 text-fg-2">
          {t('collect.channel.repeated.intro')}
        </p>
        <Button size="sm" onClick={() => dedup.mutate()} loading={dedup.isPending} className="shrink-0">
          <ScanSearch /> {t('collect.channel.repeated.redetect')}
        </Button>
      </div>
      {!rep.data.length ? (
        <Card>
          <EmptyState icon={<Repeat2 />} title={t('collect.channel.repeated.emptyTitle')} description={t('collect.channel.repeated.emptyDesc')} />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {rep.data.map((g) => (
            <Card key={g.group_id} className={cn(!g.excluded && 'opacity-90')}>
              <CardBody>
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={g.position === 'middle' ? 'info' : 'primary'}>{repeatPosition[g.position]}</Badge>
                      <span className="text-sm font-semibold">{fmtSec(g.duration)}</span>
                    </div>
                    <div className="mt-1.5 text-[13px] text-fg-3">
                      {t('collect.channel.repeated.appearsIn', <span className="font-medium text-fg">{fmtInt(g.match_count)}</span>)}
                    </div>
                  </div>
                  <Switch checked={g.excluded} onCheckedChange={(v) => toggle.mutate({ id: g.group_id, excluded: v })} label={t('collect.channel.repeated.exclude')} />
                </div>
                <div className="mt-4 text-[11px] font-medium text-fg-3">{t('collect.channel.repeated.examples')}</div>
                <ul className="mt-1.5 divide-y divide-border rounded-lg border border-border">
                  {g.examples.slice(0, 4).map((e) => (
                    <li key={e.media_id + e.start}>
                      <Link to={`/media/${e.media_id}?t=${e.start}`} className="flex items-center gap-3 px-3 py-2 text-[13px] hover:bg-surface-2/70">
                        <span className="min-w-0 flex-1 truncate">{e.media_title ?? e.media_id}</span>
                        <span className="shrink-0 font-mono text-xs text-fg-3">
                          {fmtClock(e.start)}–{fmtClock(e.end)}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

function SpeakersTab({ cid }: { cid: string }) {
  const sp = useSpeakers({ channel_id: cid, limit: 200 })
  const div = useDiversity(cid)
  const t = useT()
  const d = div.data
  const ch = d?.by_channel[0]
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card className="order-2 overflow-clip xl:order-1">
        <CardHeader title={t('collect.channel.speakers.title')} description={t('collect.channel.speakers.desc')} />
        <div className="mt-3 border-t border-border">
          {sp.isError ? <ErrorState error={sp.error} /> : !sp.data ? <ListSkeleton /> : sp.data.items.length ? <SpeakerList items={sp.data.items} /> : <EmptyState compact icon={<Users />} title={t('collect.channel.speakers.emptyTitle')} description={t('collect.channel.speakers.emptyDesc')} />}
        </div>
      </Card>
      <div className="order-1 space-y-4 xl:order-2">
        <Card>
          <CardHeader title={t('collect.channel.speakers.concentration')} description={t('collect.channel.speakers.concentrationDesc')} />
          <CardBody>
            {!ch ? (
              <Skeleton className="h-16" />
            ) : (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-semibold">{Math.round(ch.top_speaker_share * 100)}%</span>
                  <span className="text-[13px] text-fg-3">{t('collect.channel.speakers.fromOne')}</span>
                </div>
                <div className="mt-3 h-2 rounded-full bg-surface-3">
                  <div className={cn('h-full rounded-full', ch.top_speaker_share > 0.5 ? 'bg-warning' : 'bg-series-1')} style={{ width: `${ch.top_speaker_share * 100}%` }} />
                </div>
                <p className="mt-2 text-xs text-fg-3">
                  {t('collect.channel.speakers.summary', ch.speakers, d!.gini.toFixed(2))}
                </p>
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={t('collect.channel.speakers.sexAge')} />
          <CardBody className="space-y-5">
            {!d ? (
              <Skeleton className="h-24" />
            ) : (
              <>
                <StackBar
                  format={fmtHours}
                  parts={[
                    { key: 'male', label: sexLabel.male, value: d.by_sex.male ?? 0, color: 'var(--series-1)' },
                    { key: 'female', label: sexLabel.female, value: d.by_sex.female ?? 0, color: 'var(--series-2)' },
                    { key: 'unknown', label: sexLabel.unknown, value: d.by_sex.unknown ?? 0, color: 'var(--series-muted)' },
                  ]}
                />
                <HBars
                  rows={ageGroupOrder.filter((g) => d.by_age_group[g]).map((g, i) => ({ key: g, label: ageGroupLabel(g), value: d.by_age_group[g], color: ORDINAL[Math.min(2, Math.floor(i / 2))] }))}
                  format={fmtHours}
                />
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

export default function ChannelDetail() {
  const { id = '', tab } = useParams()
  const ch = useChannel(id)
  useTitle(ch.data?.title)
  const c = ch.data
  const t = useT()
  const base = `/channels/${id}`
  return (
    <Page wide>
      <Link to="/channels" className="-ml-1 mb-3 inline-flex h-8 items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 hover:bg-surface-2 hover:text-fg">
        ‹ {t('collect.channel.back')}
      </Link>
      {ch.isError ? (
        <Card>
          <ErrorState error={ch.error} onRetry={() => ch.refetch()} />
        </Card>
      ) : !c ? (
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <Skeleton className="size-16 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-7 w-56" />
              <Skeleton className="h-4 w-72" />
            </div>
          </div>
          <Skeleton className="h-20 rounded-xl" />
        </div>
      ) : (
        <>
          <Header c={c} />
          <NavTabs
            className="mb-5 mt-6"
            items={[
              { to: base, label: t('collect.channel.tabs.videos'), icon: <Film />, end: true, count: c.kind_counts?.video },
              { to: `${base}/shorts`, label: t('collect.channel.tabs.shorts'), icon: <Smartphone />, count: c.kind_counts?.short },
              { to: `${base}/playlists`, label: t('collect.channel.tabs.playlists'), icon: <ListVideo />, count: c.playlist_count },
              { to: `${base}/repeated`, label: t('collect.channel.tabs.repeated'), icon: <Repeat2 /> },
              { to: `${base}/speakers`, label: t('collect.channel.tabs.speakers'), icon: <Users />, count: c.speakers.candidates },
              { to: `${base}/quality`, label: t('collect.channel.tabs.quality'), icon: <Gauge /> },
            ]}
          />
          {!tab && <MediaBrowser key="v" channelId={id} kind={['video', 'live']} />}
          {tab === 'shorts' && <MediaBrowser key="s" channelId={id} kind={['short']} />}
          {tab === 'playlists' && <PlaylistsTab cid={id} />}
          {tab === 'repeated' && <RepeatedTab cid={id} />}
          {tab === 'speakers' && <SpeakersTab cid={id} />}
          {tab === 'quality' && <QualityPanels channelId={id} />}
        </>
      )}
    </Page>
  )
}

