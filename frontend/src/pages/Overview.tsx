import { Link } from 'react-router'
import { Activity, ArrowRight, AudioWaveform, CheckCircle2, Clock, Cpu, HardDrive, Plus, Server, Users, XCircle } from 'lucide-react'
import { useChannels, useJobs, useOverview } from '@/api/queries'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Card, CardBody, CardHeader, Skeleton, Stat } from '@/components/ui/primitives'
import { CardsSkeleton, EmptyState, ErrorState } from '@/components/ui/states'
import { JobRow } from '@/components/domain/job-row'
import { ORDINAL, StackBar } from '@/components/charts/charts'
import { fmt1, fmtBytes, fmtHours, fmtInt, fmtPct, fmtRelative, hoursUnit } from '@/lib/format'
import { queueLabel } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'

function Funnel({ hours }: { hours: { listed: number; downloaded: number; speech: number; segments: number; reviewed: number } }) {
  const t = useT()
  const steps = (['listed', 'downloaded', 'speech', 'segments', 'reviewed'] as const).map((key) => ({
    key,
    label: t(`collect.overview.funnel.${key}`),
    v: hours[key],
    hint: t(`collect.overview.funnel.${key}Hint`),
  }))
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
              {prev !== null && <div className="text-[11px] tabular text-fg-3">{t('collect.overview.funnel.retained', fmtPct(prev ? s.v / prev : 0))}</div>}
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
  const t = useT()

  return (
    <Page>
      <PageHeader
        title={t('collect.overview.title')}
        description={t('collect.overview.description')}
        actions={
          <Button asChild variant="primary">
            <Link to="/sources/new">
              <Plus /> {t('collect.overview.addSource')}
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
              { icon: <AudioWaveform />, label: t('collect.overview.stats.speech'), value: fmt1(d.hours.speech), unit: hoursUnit(), hint: t('collect.overview.stats.speechHint', d.media.ready) },
              { icon: <CheckCircle2 />, label: t('collect.overview.stats.reviewed'), value: fmt1(d.hours.reviewed), unit: hoursUnit(), hint: t('collect.overview.stats.reviewedHint', d.segments.passed, d.segments.total) },
              { icon: <Users />, label: t('collect.overview.stats.candidates'), value: fmtInt(d.speakers.candidates), unit: t('collect.overview.stats.candidatesUnit'), hint: t('collect.overview.stats.candidatesHint', d.speakers.verified) },
              { icon: <HardDrive />, label: t('collect.overview.stats.storage'), value: fmtBytes(d.storage_bytes).split(' ')[0], unit: fmtBytes(d.storage_bytes).split(' ')[1], hint: t('collect.overview.stats.storageHint', d.channels, d.media.listed) },
            ].map((s) => (
              <Card key={s.label} className="p-4 sm:p-5">
                <Stat {...s} />
              </Card>
            ))}
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title={t('collect.overview.funnelTitle')} description={t('collect.overview.funnelDesc')} />
              <CardBody>
                <Funnel hours={d.hours} />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title={t('collect.overview.workersTitle')} description={t('collect.overview.workersDesc')} icon={<Server />} />
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
                          {w.queues.map((q) => queueLabel[q]).join(t('common.listSep'))}
                          {w.current_job ? t('collect.overview.workerBusy') : t('collect.overview.workerIdle')}
                        </div>
                      </div>
                      <span className={cn('shrink-0 text-[11px]', w.alive ? 'text-fg-3' : 'font-medium text-danger-fg')}>
                        {w.alive ? t('collect.overview.workerOk') : t('collect.overview.workerOffline', fmtRelative(w.last_seen_at))}
                      </span>
                    </li>
                  ))}
                </ul>
                {d.workers.some((w) => !w.alive) && (
                  <div className="flex gap-2 rounded-lg bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-fg">
                    <XCircle className="mt-0.5 size-3.5 shrink-0" />
                    {t('collect.overview.workersDown')}
                  </div>
                )}
              </CardBody>
            </Card>
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader
                title={t('collect.overview.channelsTitle')}
                description={t('collect.overview.channelsDesc')}
                actions={
                  <Button asChild size="sm" variant="ghost">
                    <Link to="/channels">
                      {t('collect.overview.allChannels')} <ArrowRight />
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
                                  {t('collect.overview.hoursOf', fmt1(c.hours.ready), fmt1(c.hours.listed))}
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
              <CardHeader title={t('collect.overview.reviewTitle')} description={t('collect.overview.reviewDesc', d.segments.total)} />
              <CardBody>
                <StackBar
                  format={(v) => t('collect.overview.segCount', v)}
                  parts={[
                    { key: 'passed', label: t('collect.overview.passed'), value: d.segments.passed, color: 'var(--success)' },
                    { key: 'fix', label: t('collect.overview.needsFix'), value: d.segments.needs_fix, color: 'var(--warning)' },
                    { key: 'rest', label: t('collect.overview.rest'), value: Math.max(0, d.segments.total - d.segments.passed - d.segments.needs_fix), color: 'var(--series-muted)' },
                  ]}
                />
                <Button asChild variant="soft" className="mt-5 w-full">
                  <Link to="/review">
                    <AudioWaveform /> {t('collect.overview.continueReview')}
                  </Link>
                </Button>
              </CardBody>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader
              title={t('collect.overview.recentJobs')}
              icon={<Activity />}
              actions={
                <Button asChild size="sm" variant="ghost">
                  <Link to="/jobs">
                    {t('collect.overview.jobQueue')} <ArrowRight />
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
                <EmptyState compact icon={<Clock />} title={t('collect.overview.noJobs')} />
              )}
            </div>
          </Card>
          <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-fg-3">
            <Cpu className="size-3.5 shrink-0" /> {t('collect.overview.footnote')}
          </p>
        </>
      )}
    </Page>
  )
}
