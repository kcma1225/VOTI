import { intlLocale, useT } from '@/i18n'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Captions, Loader2, Plus, Tv } from 'lucide-react'
import { useChannels } from '@/api/queries'
import type { ChannelSummary } from '@/api/types'
import { fmt1, fmtCompact, fmtInt, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, Skeleton } from '@/components/ui/primitives'
import { SearchInput, Select } from '@/components/ui/form'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { Tooltip } from '@/components/ui/overlay'

function ChannelCard({ c }: { c: ChannelSummary }) {
  const t = useT()
  const L = c.counts.listed || 1
  const segs = [
    { k: 'ready', v: c.counts.ready, cls: 'bg-success', label: t('collect.channels.segReady') },
    { k: 'dl', v: Math.max(0, c.counts.downloaded - c.counts.ready), cls: 'bg-primary', label: t('collect.channels.segProcessing') },
    { k: 'cc', v: Math.max(0, c.counts.with_manual_captions - c.counts.downloaded), cls: '', label: t('collect.channels.segManualNotDl') },
  ]
  return (
    <Link
      to={`/channels/${c.id}`}
      className="group flex min-w-0 flex-col rounded-xl border border-border bg-surface p-4 shadow-xs transition-[border,box-shadow] hover:border-border-strong hover:shadow-md sm:p-5"
    >
      <div className="flex min-w-0 items-start gap-3">
        <Avatar name={c.title} src={c.thumbnail_url} size={44} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold group-hover:text-primary">{c.title}</h3>
          </div>
          <div className="mt-0.5 truncate text-xs text-fg-3">
            {c.handle} · {t('collect.channels.subscribers', fmtCompact(c.subscriber_count))}
          </div>
        </div>
        {c.list_status === 'listing' ? (
          <Badge tone="primary">
            <Loader2 className="size-3 animate-spin" /> {t('collect.channels.listing')}
          </Badge>
        ) : c.list_status === 'failed' ? (
          <Badge tone="danger" dot>
            {t('collect.channels.listFailed')}
          </Badge>
        ) : null}
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2">
        {[
          [t('collect.channels.statVideos'), fmtInt(c.counts.listed)],
          [t('collect.channels.statManual'), fmtInt(c.counts.with_manual_captions)],
          [t('collect.channels.statSpeech'), t('collect.channels.speechHours', fmt1(c.hours.speech))],
        ].map(([k, v]) => (
          <div key={k} className="min-w-0 rounded-lg bg-surface-2/70 px-2.5 py-2">
            <dt className="truncate text-[11px] text-fg-3">{k}</dt>
            <dd className="truncate text-[15px] font-semibold leading-6">{v}</dd>
          </div>
        ))}
      </dl>

      <div className="mt-4">
        <div className="flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full bg-surface-3">
          {segs.map((s) =>
            s.v > 0 ? (
              <Tooltip key={s.k} content={t('collect.channels.segTooltip', s.label, s.v)}>
                <span className={s.cls} style={{ width: `${(s.v / L) * 100}%`, background: s.k === 'cc' ? 'var(--seq-3)' : undefined }} />
              </Tooltip>
            ) : null,
          )}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-fg-3">
          <span className="flex min-w-0 items-center gap-1">
            <Captions className="size-3 shrink-0" /> <span className="truncate">{t('collect.channels.captionsChecked', c.counts.detailed, c.counts.listed)}</span>
          </span>
          <span className="shrink-0">{c.last_listed_at ? t('collect.channels.updated', fmtRelative(c.last_listed_at)) : t('collect.channels.notListed')}</span>
        </div>
      </div>
    </Link>
  )
}

export default function Channels() {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('speech')
  const t = useT()
  const ch = useChannels(q || undefined)
  const items = useMemo(() => {
    const xs = [...(ch.data?.items ?? [])]
    if (sort === 'speech') xs.sort((a, b) => b.hours.speech - a.hours.speech)
    if (sort === 'videos') xs.sort((a, b) => b.counts.listed - a.counts.listed)
    if (sort === 'recent') xs.sort((a, b) => (b.last_listed_at ?? '').localeCompare(a.last_listed_at ?? ''))
    if (sort === 'title') xs.sort((a, b) => a.title.localeCompare(b.title, intlLocale()))
    return xs
  }, [ch.data, sort])

  return (
    <Page>
      <PageHeader
        title={t('collect.channels.title')}
        description={ch.data ? t('collect.channels.description', ch.data.total) : t('collect.channels.descriptionLoading')}
        actions={
          <Button asChild variant="primary">
            <Link to="/sources/new">
              <Plus /> {t('collect.channels.addSource')}
            </Link>
          </Button>
        }
      />
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={q} onChange={setQ} placeholder={t('collect.channels.searchPlaceholder')} className="sm:max-w-xs sm:flex-1" />
        <Select value={sort} onChange={(e) => setSort(e.target.value)} className="sm:w-48" aria-label={t('collect.channels.sort')}>
          <option value="speech">{t('collect.channels.sortSpeech')}</option>
          <option value="videos">{t('collect.channels.sortVideos')}</option>
          <option value="recent">{t('collect.channels.sortRecent')}</option>
          <option value="title">{t('collect.channels.sortTitle')}</option>
        </Select>
      </div>
      {ch.isError ? (
        <Card>
          <ErrorState error={ch.error} onRetry={() => ch.refetch()} />
        </Card>
      ) : ch.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-52 rounded-xl" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Tv />}
            title={q ? t('collect.channels.emptySearch') : t('collect.channels.empty')}
            description={q ? t('collect.channels.emptySearchDesc') : t('collect.channels.emptyDesc')}
            action={
              !q && (
                <Button asChild variant="primary">
                  <Link to="/sources/new">
                    <Plus /> {t('collect.channels.addSource')}
                  </Link>
                </Button>
              )
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((c) => (
            <ChannelCard key={c.id} c={c} />
          ))}
        </div>
      )}
    </Page>
  )
}
