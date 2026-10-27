import { Link } from 'react-router'
import { Activity, ArrowRight, AudioWaveform, CheckCircle2, Clock, Cpu, HardDrive, Plus, Server, Users, XCircle } from 'lucide-react'
import { useChannels, useJobs, useOverview } from '@/api/queries'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Card, CardBody, CardHeader, Skeleton, Stat } from '@/components/ui/primitives'
import { CardsSkeleton, EmptyState, ErrorState } from '@/components/ui/states'
import { JobRow } from '@/components/domain/job-row'
import { ORDINAL, StackBar } from '@/components/charts/charts'
import { fmt1, fmtBytes, fmtHours, fmtInt, fmtPct, fmtRelative } from '@/lib/format'
import { queueLabel } from '@/lib/labels'
import { cn } from '@/lib/cn'

function Funnel({ hours }: { hours: { listed: number; downloaded: number; speech: number; segments: number; reviewed: number } }) {
  const steps = [
    { key: 'listed', label: '已列出影片', v: hours.listed, hint: '頻道內所有影片長度' },
    { key: 'downloaded', label: '已下載', v: hours.downloaded, hint: '已取得音訊與字幕' },
    { key: 'speech', label: '偵測到語音', v: hours.speech, hint: 'Brouhaha VAD' },
    { key: 'segments', label: '切分音段', v: hours.segments, hint: '字幕對齊後的音段' },
    { key: 'reviewed', label: '審查通過', v: hours.reviewed, hint: '人工確認' },
  ]
  const max = Math.max(...steps.map((s) => s.v), 1)
  return (
    <ol className="space-y-3.5">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].v : null
        const color = ORDINAL[Math.min(ORDINAL.length - 1, Math.floor((i / steps.length) * ORDINAL.length))]
        return (
          <li key={s.key} className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] items-center gap-x-4 gap-y-1 sm:grid-cols-[9rem_minmax(0,1fr)_5.5rem]">
            <div className="min-w-0">
              <div className="truncate text-[13px] font-medium text-fg">{s.label}</div>
              <div className="truncate text-[11px] text-fg-3">{s.hint}</div>
            </div>
            <div className="flex min-w-0 items-center gap-3">
              <div className="h-6 min-w-0 flex-1 rounded-md bg-surface-2">
                <div className="h-full rounded-md transition-[width] duration-700" style={{ width: `${Math.max(1, (s.v / max) * 100)}%`, background: color }} />
              </div>
              <span className="w-20 shrink-0 text-right text-[13px] font-medium tabular text-fg sm:hidden">{fmtHours(s.v * 3600)}</span>
            </div>
            <div className="hidden text-right sm:block">
              <div className="text-[13px] font-medium tabular text-fg">{fmtHours(s.v * 3600)}</div>
              {prev !== null && <div className="text-[11px] tabular text-fg-3">{fmtPct(prev ? s.v / prev : 0)} 留存</div>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export default function Overview() {
  const ov = useOverview()
  const jobs = useJobs({ limit: 8 }, 4000)
  const channels = useChannels()
  const d = ov.data

  return (
    <Page>
      <PageHeader
        title="總覽"
        description="語料收集與處理的整體進度。數字每 10 秒更新。"
        actions={
          <Button asChild variant="primary">
            <Link to="/sources/new">
              <Plus /> 新增來源
            </Link>
          </Button>
        }
      />

      {ov.isError ? (
        <Card>
          <ErrorState error={ov.error} onRetry={() => ov.refetch()} />
        </Card>
      ) : !d ? (
        <CardsSkeleton />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            {[
              { icon: <AudioWaveform />, label: '可用語音', value: fmt1(d.hours.speech), unit: '小時', hint: `${fmtInt(d.media.ready)} 部影片處理完成` },
              { icon: <CheckCircle2 />, label: '審查通過', value: fmt1(d.hours.reviewed), unit: '小時', hint: `${fmtInt(d.segments.passed)} / ${fmtInt(d.segments.total)} 音段` },
              { icon: <Users />, label: '候選語者', value: fmtInt(d.speakers.candidates), unit: '群', hint: `${fmtInt(d.speakers.verified)} 位已人工確認` },
              { icon: <HardDrive />, label: '物件儲存', value: fmtBytes(d.storage_bytes).split(' ')[0], unit: fmtBytes(d.storage_bytes).split(' ')[1], hint: `${fmtInt(d.channels)} 個頻道 · ${fmtInt(d.media.listed)} 部影片` },
            ].map((s) => (
              <Card key={s.label} className="p-4 sm:p-5">
                <Stat {...s} />
              </Card>
            ))}
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title="處理漏斗（時數）" description="每一階段保留下來的音訊時數；百分比為相對前一階段。" />
              <CardBody>
                <Funnel hours={d.hours} />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="佇列與 Worker" description="queue depth 與心跳狀態" icon={<Server />} />
              <CardBody className="space-y-4">
                <div className="grid grid-cols-4 gap-2">
                  {(['fetch', 'audio', 'ml', 'export'] as const).map((q) => (
                    <div key={q} className="rounded-lg border border-border bg-surface-2/60 px-2 py-2.5 text-center">
                      <div className="text-lg font-semibold leading-6">{fmtInt(d.queue_depth[q])}</div>
                      <div className="truncate text-[11px] text-fg-3">{queueLabel[q]}</div>
                    </div>
                  ))}
                </div>
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {d.workers.map((w) => (
                    <li key={w.name} className="flex items-center gap-3 px-3 py-2.5">
                      <span className={cn('size-2 shrink-0 rounded-full', w.alive ? 'animate-pulse-dot bg-success text-success' : 'bg-danger')} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-[12px] text-fg">{w.name}</div>
                        <div className="truncate text-[11px] text-fg-3">
                          {w.queues.map((q) => queueLabel[q]).join('、')}
                          {w.current_job ? ' · 執行中' : ' · 閒置'}
                        </div>
                      </div>
                      <span className={cn('shrink-0 text-[11px]', w.alive ? 'text-fg-3' : 'font-medium text-danger-fg')}>
                        {w.alive ? '正常' : `離線 ${fmtRelative(w.last_seen_at)}`}
                      </span>
                    </li>
                  ))}
                </ul>
                {d.workers.some((w) => !w.alive) && (
                  <div className="flex gap-2 rounded-lg bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-fg">
                    <XCircle className="mt-0.5 size-3.5 shrink-0" />
                    有服務沒有回應心跳，相關工作會停在排隊狀態。請檢查 docker compose 日誌。
                  </div>
                )}
              </CardBody>
            </Card>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader
                title="頻道進度"
                description="可用時數 / 已列出時數"
                actions={
                  <Button asChild size="sm" variant="ghost">
                    <Link to="/channels">
                      全部頻道 <ArrowRight />
                    </Link>
                  </Button>
                }
              />
              <div className="px-2 pb-2 pt-2 sm:px-3">
                {channels.isLoading ? (
                  <div className="space-y-3 p-3">
                    {[0, 1, 2, 3].map((i) => (
                      <Skeleton key={i} className="h-10" />
                    ))}
                  </div>
                ) : (
                  <ul>
                    {channels.data?.items.map((c) => {
                      const share = c.hours.listed ? c.hours.ready / c.hours.listed : 0
                      return (
                        <li key={c.id}>
                          <Link to={`/channels/${c.id}`} className="flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-surface-2">
                            <Avatar name={c.title} src={c.thumbnail_url} size={34} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-baseline justify-between gap-3">
                                <span className="truncate text-[13px] font-medium">{c.title}</span>
                                <span className="shrink-0 text-xs tabular text-fg-3">
                                  {fmt1(c.hours.ready)} / {fmt1(c.hours.listed)} 小時
                                </span>
                              </div>
                              <div className="mt-1.5 h-1.5 rounded-full bg-surface-3">
                                <div className="h-full rounded-full bg-series-1" style={{ width: `${Math.max(1, share * 100)}%` }} />
                              </div>
                            </div>
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </Card>

            <Card>
              <CardHeader title="審查進度" description={`${fmtInt(d.segments.total)} 個音段`} />
              <CardBody>
                <StackBar
                  format={(v) => `${fmtInt(v)} 段`}
                  parts={[
                    { key: 'passed', label: '通過', value: d.segments.passed, color: 'var(--success)' },
                    { key: 'fix', label: '需修正', value: d.segments.needs_fix, color: 'var(--warning)' },
                    { key: 'rest', label: '未審查／排除', value: Math.max(0, d.segments.total - d.segments.passed - d.segments.needs_fix), color: 'var(--series-muted)' },
                  ]}
                />
                <Button asChild variant="soft" className="mt-5 w-full">
                  <Link to="/review">
                    <AudioWaveform /> 繼續審查
                  </Link>
                </Button>
              </CardBody>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader
              title="最近的工作"
              icon={<Activity />}
              actions={
                <Button asChild size="sm" variant="ghost">
                  <Link to="/jobs">
                    工作佇列 <ArrowRight />
                  </Link>
                </Button>
              }
            />
            <div className="mt-2 grid divide-y divide-border border-t border-border lg:grid-cols-2 lg:divide-y-0">
              {jobs.data?.items.length ? (
                jobs.data.items.map((j, i) => (
                  <JobRow key={j.id} job={j} className={cn(i % 2 === 0 && 'lg:border-r', i >= 2 && 'lg:border-t', 'border-border')} />
                ))
              ) : jobs.isLoading ? (
                <div className="p-4">
                  <Skeleton className="h-10" />
                </div>
              ) : (
                <EmptyState compact icon={<Clock />} title="還沒有任何工作" />
              )}
            </div>
          </Card>
          <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-fg-3">
            <Cpu className="size-3.5" /> 時數為各階段音訊長度總和；候選語者為最近一次分群的群數，不等於實際人數。
          </p>
        </>
      )}
    </Page>
  )
}
