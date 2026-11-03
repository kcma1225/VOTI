import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { GitMerge, History, Sparkles, Users } from 'lucide-react'
import { api } from '@/api/client'
import { useT } from '@/i18n'
import { onErr, useChannels, useClusterRuns, useSpeakers } from '@/api/queries'
import { fmtDateTime, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody } from '@/components/ui/primitives'
import { Chip, Field, SearchInput, Segmented, Select, Slider } from '@/components/ui/form'
import { Dialog, Popover, Tooltip } from '@/components/ui/overlay'
import { Pagination } from '@/components/ui/data-table'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { SpeakerList } from '@/components/domain/speakers'
import { SpeakerSimilarityView } from '@/components/domain/speaker-map'

const LIMIT = 50

function ClusterDialog({ open, onOpenChange, current }: { open: boolean; onOpenChange: (o: boolean) => void; current?: number }) {
  const [th, setTh] = useState(current ?? 0.6)
  const [scope, setScope] = useState<string[]>([])
  const ch = useChannels()
  const qc = useQueryClient()
  const t = useT()
  const run = useMutation({
    mutationFn: () => api.cluster(th, scope),
    onSuccess: () => {
      toast.success(t('people.cluster.started'), { description: t('people.cluster.startedDesc') })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      onOpenChange(false)
    },
    onError: onErr,
  })
  const desc = th < 0.6 ? t('people.cluster.loose') : th > 0.82 ? t('people.cluster.strict') : t('people.cluster.balanced')
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('people.cluster.title')}
      description={t('people.cluster.description')}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}>
            <GitMerge /> {t('people.cluster.start')}
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        <Field label={t('people.cluster.threshold')} aside={th.toFixed(2)} hint={desc}>
          <Slider aria-label={t('people.cluster.threshold')} value={[th]} onValueChange={([v]) => setTh(v)} min={0.3} max={0.9} step={0.01} />
          <div className="flex justify-between text-[11px] text-fg-3">
            <span>{t('people.cluster.fewer')}</span>
            <span>{t('people.cluster.more')}</span>
          </div>
        </Field>
        <Field label={t('people.cluster.scope')} hint={t('people.cluster.scopeHint')}>
          <div className="flex flex-wrap gap-1.5">
            {ch.data?.items.map((c) => (
              <Chip key={c.id} active={scope.includes(c.id)} onClick={() => setScope((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id]))}>
                {c.title}
              </Chip>
            ))}
          </div>
        </Field>
      </div>
    </Dialog>
  )
}

export default function Speakers() {
  const [q, setQ] = useState('')
  const [channel, setChannel] = useState('')
  const [sort, setSort] = useState('-total_sec')
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState(false)
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'map' ? 'map' : 'list'
  const setView = (v: 'list' | 'map') =>
    setParams(
      (p) => {
        if (v === 'map') p.set('view', 'map')
        else p.delete('view')
        return p
      },
      { replace: true },
    )
  const t = useT()
  const runs = useClusterRuns()
  const ch = useChannels()
  const cur = runs.data?.find((r) => r.is_current)
  const sp = useSpeakers({ q: q || undefined, channel_id: channel || undefined, sort, limit: LIMIT, offset })

  return (
    <Page>
      <PageHeader
        title={t('people.speakers.title')}
        description={t('people.speakers.description')}
        actions={
          <Button variant="primary" onClick={() => setOpen(true)}>
            <GitMerge /> {t('people.speakers.runClustering')}
          </Button>
        }
      />

      <Card className="mb-4">
        <CardBody className="flex flex-col gap-4 py-4 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-medium">{t('people.speakers.currentRun')}</span>
              {cur && <Badge tone="success" dot>{t('people.speakers.inUse')}</Badge>}
              {cur && !cur.created_by && (
                <Tooltip content={t('people.speakers.autoHint')}>
                  <Badge tone="neutral">
                    <Sparkles className="size-3" /> {t('people.speakers.auto')}
                  </Badge>
                </Tooltip>
              )}
            </div>
            <div className="mt-1 text-[13px] text-fg-3">
              {cur ? (
                <>
                  {t('people.speakers.threshold')} <span className="font-medium tabular text-fg">{cur.threshold.toFixed(2)}</span> · {t('people.speakers.clusters', cur.stats.speakers ?? 0)} · {t('people.speakers.segments', cur.stats.segments ?? 0)} ·{' '}
                  {cur.scope.channel_ids?.length ? t('people.speakers.channels', cur.scope.channel_ids.length) : t('people.speakers.allChannels')} · {fmtRelative(cur.created_at)}
                </>
              ) : (
                t('people.speakers.noRun')
              )}
            </div>
          </div>
          {runs.data && runs.data.length > 1 && (
            <Popover
              align="end"
              trigger={
                <Button size="sm" variant="ghost">
                  <History /> {t('people.speakers.history', runs.data.length)}
                </Button>
              }
            >
              <div className="mb-2 text-[13px] font-semibold">{t('people.speakers.historyTitle')}</div>
              <ul className="divide-y divide-border">
                {runs.data.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                    <span>
                      {t('people.speakers.threshold')} {r.threshold.toFixed(2)} · {t('people.speakers.clusters', r.stats.speakers ?? 0)}
                      {!r.created_by && <span className="text-fg-3"> · {t('people.speakers.auto')}</span>}
                      <span className="block text-[11px] text-fg-3">{fmtDateTime(r.created_at)}</span>
                    </span>
                    {r.is_current && <Badge tone="success">{t('people.speakers.inUse')}</Badge>}
                  </li>
                ))}
              </ul>
            </Popover>
          )}
        </CardBody>
      </Card>

      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <Segmented
          aria-label={t('people.speakers.viewMap')}
          value={view}
          onChange={setView}
          options={[
            { value: 'list', label: t('people.speakers.viewList') },
            { value: 'map', label: t('people.speakers.viewMap') },
          ]}
          className="self-start"
        />
        {view === 'list' && (
          <SearchInput value={q} onChange={(v) => (setQ(v), setOffset(0))} placeholder={t('people.speakers.searchPlaceholder')} className="sm:max-w-xs sm:flex-1" />
        )}
        <Select value={channel} onChange={(e) => (setChannel(e.target.value), setOffset(0))} className="sm:w-48" aria-label={t('people.speakers.channel')}>
          <option value="">{t('people.speakers.allChannelsOption')}</option>
          {ch.data?.items.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </Select>
        {view === 'list' && (
          <Select value={sort} onChange={(e) => setSort(e.target.value)} className="sm:w-40" aria-label={t('people.speakers.sort')}>
            <option value="-total_sec">{t('people.speakers.sortHours')}</option>
            <option value="-segment_count">{t('people.speakers.sortSegments')}</option>
            <option value="label">{t('people.speakers.sortLabel')}</option>
          </Select>
        )}
      </div>

      {view === 'map' ? (
        <SpeakerSimilarityView channelId={channel || undefined} />
      ) : (
        <Card className="overflow-clip">
          {sp.isError ? (
            <ErrorState error={sp.error} onRetry={() => sp.refetch()} />
          ) : !sp.data ? (
            <ListSkeleton rows={8} />
          ) : !sp.data.items.length ? (
            runs.data && !cur ? (
              <EmptyState
                icon={<Users />}
                title={t('people.speakers.noRunTitle')}
                description={t('people.speakers.noRunDesc')}
                action={
                  <Button variant="primary" onClick={() => setOpen(true)}>
                    <GitMerge /> {t('people.speakers.runClustering')}
                  </Button>
                }
              />
            ) : (
              <EmptyState icon={<Users />} title={t('people.speakers.emptyTitle')} description={t('people.speakers.emptyDesc')} />
            )
          ) : (
            <>
              <SpeakerList items={sp.data.items} />
              <Pagination total={sp.data.total} limit={LIMIT} offset={offset} onChange={setOffset} className="border-t border-border" />
            </>
          )}
        </Card>
      )}
      <ClusterDialog open={open} onOpenChange={setOpen} current={cur?.threshold} />
    </Page>
  )
}
