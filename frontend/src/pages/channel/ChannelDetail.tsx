import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
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
  Users,
} from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, onErr, qk, useChannel, useDiversity, usePlaylists, useRepeated, useSpeakers } from '@/api/queries'
import type { ChannelDetail as TChannel } from '@/api/types'
import { fmt1, fmtClock, fmtCompact, fmtHours, fmtInt, fmtMos, fmtRelative, fmtSec } from '@/lib/format'
import { ageGroupLabel, ageGroupOrder, repeatPosition, sexLabel } from '@/lib/labels'
import { Page } from '@/components/layout/page'
import { useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { NavTabs } from '@/components/ui/tabs'
import { Dialog, Menu, MenuContent, MenuItem, MenuTrigger } from '@/components/ui/overlay'
import { Switch, Textarea } from '@/components/ui/form'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { MediaBrowser } from '@/components/domain/media-browser'
import { QualityPanels } from '@/components/domain/quality-viz'
import { SpeakerList } from '@/components/domain/speakers'
import { HBars, ORDINAL, StackBar } from '@/components/charts/charts'
import { cn } from '@/lib/cn'

function Header({ c }: { c: TChannel }) {
  const qc = useQueryClient()
  const [notesOpen, setNotesOpen] = useState(false)
  const [notes, setNotes] = useState(c.notes)
  const refresh = useMutation({
    mutationFn: () => api.refreshChannel(c.id),
    onSuccess: () => {
      toast.success('已開始重新列出頻道影片')
      qc.invalidateQueries({ queryKey: qk.channel(c.id) })
    },
    onError: onErr,
  })
  const detail = useMutation({
    mutationFn: () => api.detailChannel(c.id),
    onSuccess: (r) => toast.success(r.queued ? `已排入 ${fmtInt(r.queued)} 部影片取得字幕資訊` : '所有影片都已取得詳細資訊'),
    onError: onErr,
  })
  const dedup = useMutation({ mutationFn: () => api.dedup(c.id), onSuccess: () => toast.success('已開始偵測重複片段'), onError: onErr })
  const saveNotes = useMutation({
    mutationFn: () => api.patchChannel(c.id, notes),
    onSuccess: (d) => {
      qc.setQueryData(qk.channel(c.id), d)
      setNotesOpen(false)
      toast.success('已儲存筆記')
    },
    onError: (e) => toast.error(errMsg(e)),
  })
  const stats = [
    { label: '影片', value: fmtInt(c.kind_counts?.video ?? c.counts.listed), hint: c.video_count && c.video_count > c.counts.listed ? `YouTube 顯示 ${fmtInt(c.video_count)}` : undefined },
    { label: 'Shorts', value: fmtInt(c.kind_counts?.short ?? 0) },
    { label: '有原生字幕', value: fmtInt(c.counts.with_manual_captions), hint: `${fmtInt(c.counts.detailed)} 部已檢查` },
    { label: '已下載', value: fmtInt(c.counts.downloaded), hint: c.counts.failed ? `${fmtInt(c.counts.failed)} 部失敗` : undefined, warn: !!c.counts.failed },
    { label: '可用語音', value: `${fmt1(c.hours.speech)}`, unit: '小時', hint: `影片總長 ${fmt1(c.hours.listed)} 小時` },
    { label: '候選語者', value: fmtInt(c.speakers.candidates), hint: fmtHours(c.speakers.total_sec) },
    { label: '平均 SNR', value: c.quality.snr_mean != null ? fmt1(c.quality.snr_mean) : '—', unit: 'dB', hint: `DNSMOS ${fmtMos(c.quality.dnsmos_ovrl)}` },
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
                    <Loader2 className="size-3 animate-spin" /> 正在列出影片
                  </Badge>
                )}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-fg-3">
                <span>{c.handle}</span>
                <span>{fmtCompact(c.subscriber_count)} 位訂閱者</span>
                <span>更新於{fmtRelative(c.last_listed_at)}</span>
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
            <RefreshCw /> 重新列出
          </Button>
          <Button onClick={() => detail.mutate()} loading={detail.isPending} className="hidden sm:inline-flex">
            <Captions /> 檢查字幕
          </Button>
          <Menu>
            <MenuTrigger asChild>
              <Button size="icon" aria-label="更多動作">
                <MoreHorizontal />
              </Button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem icon={<Captions />} onSelect={() => detail.mutate()}>
                檢查所有未檢查影片的字幕
              </MenuItem>
              <MenuItem icon={<ScanSearch />} onSelect={() => dedup.mutate()}>
                重新偵測重複片段
              </MenuItem>
              <MenuItem icon={<NotebookPen />} onSelect={() => setNotesOpen(true)}>
                編輯研究筆記
              </MenuItem>
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

      <Dialog
        open={notesOpen}
        onOpenChange={(o) => {
          setNotesOpen(o)
          if (o) setNotes(c.notes)
        }}
        title="研究筆記"
        description="記錄這個頻道的語者組成、錄音環境或授權注意事項。"
        footer={
          <>
            <Button onClick={() => setNotesOpen(false)}>取消</Button>
            <Button variant="primary" loading={saveNotes.isPending} onClick={() => saveNotes.mutate()}>
              儲存
            </Button>
          </>
        }
      >
        <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={6} placeholder="例如：主持人固定兩位；來賓多為 60 歲以上漁民…" />
      </Dialog>
    </>
  )
}

function PlaylistsTab({ cid }: { cid: string }) {
  const pl = usePlaylists(cid)
  if (pl.isError) return <ErrorState error={pl.error} onRetry={() => pl.refetch()} />
  if (!pl.data) return <ListSkeleton rows={4} />
  if (!pl.data.items.length)
    return (
      <Card>
        <EmptyState icon={<ListVideo />} title="沒有播放清單" description="這個頻道沒有公開的播放清單，或尚未列出。" />
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
              {fmtInt(p.media_count)} 部影片 · 更新於{fmtRelative(p.last_listed_at)}
            </div>
          </div>
        </Link>
      ))}
    </div>
  )
}

function RepeatedTab({ cid }: { cid: string }) {
  const rep = useRepeated(cid)
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: ({ id, excluded }: { id: string; excluded: boolean }) => api.patchRepeated(id, excluded),
    onSuccess: (_d, v) => {
      qc.setQueryData(qk.repeated(cid), (old: typeof rep.data) => old?.map((g) => (g.group_id === v.id ? { ...g, excluded: v.excluded } : g)))
      toast.success(v.excluded ? '已排除此重複片段（音段的重疊比例會重新計算）' : '已取消排除')
    },
    onError: onErr,
  })
  const dedup = useMutation({ mutationFn: () => api.dedup(cid), onSuccess: () => toast.success('已開始重新偵測'), onError: onErr })
  if (rep.isError) return <ErrorState error={rep.error} onRetry={() => rep.refetch()} />
  if (!rep.data) return <ListSkeleton rows={3} />
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2/50 p-4 sm:flex-row sm:items-center">
        <Repeat2 className="hidden size-5 shrink-0 text-fg-3 sm:block" />
        <p className="min-w-0 flex-1 text-[13px] leading-5 text-fg-2">
          以音訊指紋比對同頻道影片，找出反覆出現的片頭、片尾與固定橋段。被排除的片段在建立資料集時會依「重疊比例」過濾，避免同一段聲音大量重複。
        </p>
        <Button size="sm" onClick={() => dedup.mutate()} loading={dedup.isPending} className="shrink-0">
          <ScanSearch /> 重新偵測
        </Button>
      </div>
      {!rep.data.length ? (
        <Card>
          <EmptyState icon={<Repeat2 />} title="沒有發現重複片段" description="需要至少數部已處理完成的影片才能比對。" />
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
                      出現在 <span className="font-medium text-fg">{fmtInt(g.match_count)}</span> 部影片
                    </div>
                  </div>
                  <Switch checked={g.excluded} onCheckedChange={(v) => toggle.mutate({ id: g.group_id, excluded: v })} label="排除" />
                </div>
                <div className="mt-4 text-[11px] font-medium text-fg-3">範例</div>
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
  const d = div.data
  const ch = d?.by_channel[0]
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card className="order-2 overflow-clip xl:order-1">
        <CardHeader title="候選語者" description="最近一次分群結果中出現在此頻道的語者群" />
        <div className="mt-3 border-t border-border">
          {sp.isError ? <ErrorState error={sp.error} /> : !sp.data ? <ListSkeleton /> : sp.data.items.length ? <SpeakerList items={sp.data.items} /> : <EmptyState compact icon={<Users />} title="尚無語者資料" description="影片處理完成並執行語者分群後才會出現。" />}
        </div>
      </Card>
      <div className="order-1 space-y-4 xl:order-2">
        <Card>
          <CardHeader title="語者集中度" description="時數最多的語者佔比越高，代表頻道越依賴少數人" />
          <CardBody>
            {!ch ? (
              <Skeleton className="h-16" />
            ) : (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-semibold">{Math.round(ch.top_speaker_share * 100)}%</span>
                  <span className="text-[13px] text-fg-3">來自單一語者</span>
                </div>
                <div className="mt-3 h-2 rounded-full bg-surface-3">
                  <div className={cn('h-full rounded-full', ch.top_speaker_share > 0.5 ? 'bg-warning' : 'bg-series-1')} style={{ width: `${ch.top_speaker_share * 100}%` }} />
                </div>
                <p className="mt-2 text-xs text-fg-3">
                  {fmtInt(ch.speakers)} 位候選語者 · Gini {d!.gini.toFixed(2)}
                </p>
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="性別與年齡（時數）" />
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
  const base = `/channels/${id}`
  return (
    <Page wide>
      <Link to="/channels" className="-ml-1 mb-3 inline-flex h-8 items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 hover:bg-surface-2 hover:text-fg">
        ‹ 頻道
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
              { to: base, label: '影片', icon: <Film />, end: true, count: c.kind_counts?.video },
              { to: `${base}/shorts`, label: 'Shorts', icon: <Smartphone />, count: c.kind_counts?.short },
              { to: `${base}/playlists`, label: '播放清單', icon: <ListVideo />, count: c.playlist_count },
              { to: `${base}/repeated`, label: '重複片段', icon: <Repeat2 /> },
              { to: `${base}/speakers`, label: '語者', icon: <Users />, count: c.speakers.candidates },
              { to: `${base}/quality`, label: '音質', icon: <Gauge /> },
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

