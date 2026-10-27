import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowRight, CheckCircle2, Film, Link2, ListVideo, Smartphone, Tv } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, useJobs } from '@/api/queries'
import type { ResolveResult } from '@/api/types'
import { classifyUrl } from '@/lib/url'
import { fmtRelative } from '@/lib/format'
import { jobKind } from '@/lib/labels'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Card, CardBody, CardHeader, Kbd } from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/domain/status'
import { cn } from '@/lib/cn'
import { t as tg, useT } from '@/i18n'

const EXAMPLES = () => [
  { label: tg('collect.addSource.examples.channel'), url: 'https://www.youtube.com/@ptstaigi' },
  { label: tg('collect.addSource.examples.channelVideos'), url: 'https://www.youtube.com/@amaskitchen/videos' },
  { label: tg('collect.addSource.examples.playlist'), url: 'https://www.youtube.com/playlist?list=PLx9fQ0abcdEFG' },
  { label: tg('collect.addSource.examples.video'), url: 'https://youtu.be/dQw4w9WgXcQ' },
  { label: tg('collect.addSource.examples.shorts'), url: 'https://www.youtube.com/shorts/aBcDeFgHiJk' },
]

function Classification({ url }: { url: string }) {
  const t = useT()
  const c = useMemo(() => classifyUrl(url), [url])
  if (c.kind === 'empty')
    return <p className="text-[13px] text-fg-3">{t('collect.addSource.emptyHint')}</p>
  if (c.kind === 'invalid')
    return (
      <p className="flex items-center gap-2 text-[13px] text-danger-fg">
        <AlertCircle className="size-4 shrink-0" />
        {c.reason}
      </p>
    )
  const map = {
    channel: { icon: <Tv />, title: t('collect.addSource.kindChannel'), body: t('collect.addSource.kindChannelBody', c.kind === 'channel' ? c.handle : ''), tone: 'text-series-1' },
    playlist: { icon: <ListVideo />, title: t('collect.addSource.kindPlaylist'), body: c.kind === 'playlist' && c.withVideo ? t('collect.addSource.kindPlaylistWithVideo') : t('collect.addSource.kindPlaylistBody'), tone: 'text-series-7' },
    video: { icon: c.kind === 'video' && c.short ? <Smartphone /> : <Film />, title: c.kind === 'video' && c.short ? t('collect.addSource.kindShort') : t('collect.addSource.kindVideo'), body: t('collect.addSource.kindVideoBody', c.kind === 'video' ? c.id : ''), tone: 'text-series-3' },
  }[c.kind]
  return (
    <div className="flex min-w-0 items-start gap-3">
      <div className={cn('grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 [&_svg]:size-[18px]', map.tone)}>{map.icon}</div>
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
          <CheckCircle2 className="size-3.5 text-success" /> {t('collect.addSource.detected', map.title)}
        </div>
        <div className="mt-0.5 truncate text-xs text-fg-3">{map.body}</div>
      </div>
    </div>
  )
}

function ResultCard({ r }: { r: ResolveResult }) {
  const to = r.channel ? `/channels/${r.channel.id}` : r.media ? `/media/${r.media.id}` : r.playlist?.channel_id ? `/channels/${r.playlist.channel_id}/playlists` : '/channels'
  const title = r.channel?.title ?? r.playlist?.title ?? r.media?.title ?? ''
  const t = useT()
  return (
    <Card className="mt-4 border-success/30 animate-slide-up">
      <CardBody className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <Avatar name={title} size={44} rounded="lg" />
        <div className="min-w-0 flex-1">
          <div className="text-xs text-fg-3">{r.type === 'channel' ? t('collect.addSource.addedChannel') : r.type === 'playlist' ? t('collect.addSource.addedPlaylist') : t('collect.addSource.addedVideo')}</div>
          <div className="truncate text-[15px] font-semibold">{title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-3">
            <JobStatusBadge status={r.job.status} /> {jobKind[r.job.kind]}
            {r.channel && <span>{t('collect.addSource.knownVideos', r.channel.counts.listed)}</span>}
          </div>
        </div>
        <Button asChild variant="primary">
          <Link to={to}>
            {t('collect.addSource.goFilter')} <ArrowRight />
          </Link>
        </Button>
      </CardBody>
    </Card>
  )
}

export default function AddSource() {
  const [url, setUrl] = useState('')
  const t = useT()
  const qc = useQueryClient()
  const c = classifyUrl(url)
  const recent = useJobs({ kind: ['source.resolve', 'channel.list', 'playlist.list'], limit: 6 }, 5000)
  const m = useMutation({
    mutationFn: (u: string) => api.resolve(u),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['channels'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (c.kind === 'empty' || c.kind === 'invalid') return
    m.mutate(url.trim())
  }

  return (
    <Page>
      <PageHeader title={t('collect.addSource.title')} description={t('collect.addSource.description')} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <Card>
            <form onSubmit={submit}>
              <CardBody className="space-y-4">
                <label htmlFor="url" className="text-[13px] font-medium">
                  {t('collect.addSource.urlLabel')}
                </label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <div className="relative min-w-0 flex-1">
                    <Link2 className="pointer-events-none absolute left-3.5 top-1/2 size-[18px] -translate-y-1/2 text-fg-3" />
                    <input
                      id="url"
                      name="url"
                      value={url}
                      onChange={(e) => {
                        setUrl(e.target.value)
                        m.reset()
                      }}
                      placeholder="https://www.youtube.com/@channel"
                      autoComplete="off"
                      spellCheck={false}
                      inputMode="url"
                      autoFocus
                      className={cn(
                        'h-12 w-full rounded-xl border bg-surface pl-11 pr-4 text-[15px] shadow-xs outline-none transition-[border,box-shadow] placeholder:text-fg-3 focus:ring-4',
                        c.kind === 'invalid' ? 'border-danger/60 focus:ring-danger/15' : 'border-border-strong focus:border-primary focus:ring-ring/20',
                      )}
                    />
                  </div>
                  <Button type="submit" variant="primary" size="lg" className="h-12 sm:h-12 sm:px-6" disabled={c.kind === 'empty' || c.kind === 'invalid'} loading={m.isPending}>
                    {t('collect.addSource.submit')}
                  </Button>
                </div>
                <div className="min-h-10 rounded-lg bg-surface-2/70 px-3 py-2.5">
                  <Classification url={url} />
                </div>
                {m.isError && (
                  <div className="flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2.5 text-[13px] text-danger-fg">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" /> {errMsg(m.error)}
                  </div>
                )}
                <div>
                  <div className="mb-2 text-xs text-fg-3">{t('collect.addSource.examplesLabel')}</div>
                  <div className="flex flex-wrap gap-2">
                    {EXAMPLES().map((e) => (
                      <button
                        key={e.url}
                        type="button"
                        onClick={() => setUrl(e.url)}
                        className="inline-flex h-9 max-w-full items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-xs text-fg-2 hover:border-border-strong hover:text-fg sm:h-8"
                      >
                        <span className="font-medium text-fg">{e.label}</span>
                        <span className="hidden truncate text-fg-3 sm:inline">{e.url.replace('https://www.', '')}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </CardBody>
            </form>
          </Card>
          {m.data && <ResultCard r={m.data} />}

          <Card className="mt-4">
            <CardHeader title={t('collect.addSource.recentTitle')} description={t('collect.addSource.recentDesc')} />
            <ul className="mt-2 divide-y divide-border border-t border-border">
              {recent.data?.items.length ? (
                recent.data.items.map((j) => (
                  <li key={j.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{j.channel_title ?? j.media_title ?? '—'}</div>
                      <div className="text-xs text-fg-3">
                        {jobKind[j.kind]} · {fmtRelative(j.created_at)}
                      </div>
                    </div>
                    <JobStatusBadge status={j.status} />
                  </li>
                ))
              ) : (
                <li className="px-5 py-6 text-center text-[13px] text-fg-3">{t('collect.addSource.recentEmpty')}</li>
              )}
            </ul>
          </Card>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader title={t('collect.addSource.nextTitle')} />
            <CardBody>
              <ol className="space-y-4 text-[13px]">
                {[
                  [t('collect.addSource.steps.list'), t('collect.addSource.steps.listDesc')],
                  [t('collect.addSource.steps.detail'), t('collect.addSource.steps.detailDesc')],
                  [t('collect.addSource.steps.filter'), t('collect.addSource.steps.filterDesc')],
                  [t('collect.addSource.steps.process'), t('collect.addSource.steps.processDesc')],
                ].map(([title, d], i) => (
                  <li key={title} className="flex gap-3">
                    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary-soft text-xs font-semibold text-primary-soft-fg">{i + 1}</span>
                    <div>
                      <div className="font-medium">{title}</div>
                      <div className="mt-0.5 text-xs leading-5 text-fg-3">{d}</div>
                    </div>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
          <p className="px-1 text-xs leading-5 text-fg-3">
            {t('collect.addSource.tip', <Kbd>Enter</Kbd>)}
          </p>
        </aside>
      </div>
    </Page>
  )
}
