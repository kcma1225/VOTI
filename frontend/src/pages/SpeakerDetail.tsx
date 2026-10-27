import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { BadgeCheck, Loader2, Pause, Pencil, Play } from 'lucide-react'
import { api } from '@/api/client'
import { useT } from '@/i18n'
import { onErr, qk, useSpeaker } from '@/api/queries'
import { fmtDb, fmtHours, fmtMos } from '@/lib/format'
import { ageGroupLabel, ageGroupOrder, emotionLabel, sexLabel, voiceQualityLabel } from '@/lib/labels'
import { useClipPlayer } from '@/hooks/useClipPlayer'
import { Page, useTitle } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { Input } from '@/components/ui/form'
import { ErrorState } from '@/components/ui/states'
import { HBars, ORDINAL, StackBar } from '@/components/charts/charts'
import { ReviewBadge } from '@/components/domain/status'
import { speakerName } from '@/components/domain/speakers'
import { cn } from '@/lib/cn'

export default function SpeakerDetail() {
  const { id = '' } = useParams()
  const sp = useSpeaker(id)
  const t = useT()
  const s = sp.data
  useTitle(s ? speakerName(s) : undefined)
  const qc = useQueryClient()
  const player = useClipPlayer()
  const [editing, setEditing] = useState(false)
  const [all, setAll] = useState(false)
  const [name, setName] = useState('')
  useEffect(() => setName(s?.display_name ?? ''), [s?.display_name])
  const patch = useMutation({
    mutationFn: (b: { display_name?: string | null; verified?: boolean }) => api.patchSpeaker(id, b),
    onSuccess: (d) => {
      qc.setQueryData(qk.speaker(id), (old: typeof s) => (old ? { ...old, ...d } : old))
      qc.invalidateQueries({ queryKey: ['speakers'] })
      setEditing(false)
      toast.success(t('people.speakerDetail.updated'))
    },
    onError: onErr,
  })

  if (sp.isError)
    return (
      <Page>
        <Card>
          <ErrorState error={sp.error} onRetry={() => sp.refetch()} />
        </Card>
      </Page>
    )
  if (!s)
    return (
      <Page>
        <Skeleton className="h-20 w-80" />
        <Skeleton className="mt-6 h-96 rounded-xl" />
      </Page>
    )
  const p = s.profile
  return (
    <Page>
      <Link to="/speakers" className="-ml-1 mb-3 inline-flex h-8 items-center gap-1 rounded-md pl-0.5 pr-2 text-[13px] text-fg-3 hover:bg-surface-2 hover:text-fg">
        ‹ {t('people.speakerDetail.back')}
      </Link>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <Avatar name={speakerName(s)} size={60} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex max-w-md gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                patch.mutate({ display_name: name.trim() || null })
              }}
            >
              <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={t('people.speakerDetail.namePlaceholder')} />
              <Button type="submit" variant="primary" loading={patch.isPending}>
                {t('common.save')}
              </Button>
              <Button onClick={() => setEditing(false)}>{t('common.cancel')}</Button>
            </form>
          ) : (
            <div className="flex min-w-0 items-center gap-2">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{speakerName(s)}</h1>
              {s.verified && <BadgeCheck className="size-5 shrink-0 text-primary" />}
              <Button size="icon-sm" variant="ghost" aria-label={t('people.speakerDetail.rename')} onClick={() => setEditing(true)}>
                <Pencil />
              </Button>
            </div>
          )}
          <div className="mt-1 flex flex-wrap gap-x-3 text-[13px] text-fg-3">
            <span className="font-mono">{s.label}</span>
            <span>{fmtHours(s.total_sec)}</span>
            <span>{t('people.speakerDetail.segments', s.segment_count)}</span>
            <span>{t('people.speakerDetail.videos', s.media_count)}</span>
          </div>
        </div>
        <Button variant={s.verified ? 'secondary' : 'primary'} onClick={() => patch.mutate({ verified: !s.verified })} loading={patch.isPending && !editing}>
          <BadgeCheck /> {s.verified ? t('people.speakerDetail.unverify') : t('people.speakerDetail.verify')}
        </Button>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="order-2 xl:order-1">
          <CardHeader title={t('people.speakerDetail.samples')} description={t('people.speakerDetail.samplesDesc')} />
          <ul className="mt-3 divide-y divide-border border-t border-border">
            {s.segments.slice(0, all ? 50 : 12).map((x) => {
              const on = player.playing === x.id
              return (
                <li key={x.id} className={cn('flex items-start gap-3 px-4 py-3 sm:px-5', on && 'bg-primary-soft/50')}>
                  <Button size="icon" variant={on ? 'primary' : 'secondary'} className="mt-0.5 rounded-full" aria-label={on ? t('people.speakerDetail.pause') : t('people.speakerDetail.play')} onClick={() => player.toggle(x.id)}>
                    {player.loading === x.id ? <Loader2 className="animate-spin" /> : on ? <Pause /> : <Play className="translate-x-px" />}
                  </Button>
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] leading-5 break-cjk">{x.text}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-fg-3">
                      <Link to={`/review?media=${x.media_id}&segment=${x.id}&status=all`} className="max-w-full truncate hover:text-primary">
                        {x.media_title}
                      </Link>
                      <span className="tabular">{t('people.speakerDetail.seconds', x.duration.toFixed(1))}</span>
                      <span className="tabular">SNR {fmtDb(x.snr)}</span>
                      <span className="tabular">MOS {fmtMos(x.dnsmos_ovrl)}</span>
                    </div>
                  </div>
                  <ReviewBadge status={x.review_status} className="hidden sm:inline-flex" />
                </li>
              )
            })}
          </ul>
          {s.segments.length > 12 && (
            <div className="border-t border-border p-2 text-center">
              <Button variant="ghost" size="sm" onClick={() => setAll((a) => !a)}>
                {all ? t('people.speakerDetail.showFirst') : t('people.speakerDetail.showAll', s.segments.length)}
              </Button>
            </div>
          )}
        </Card>

        <div className="order-1 space-y-4 xl:order-2">
          <Card>
            <CardHeader title={t('people.speakerDetail.traits')} description={t('people.speakerDetail.traitsDesc')} />
            <CardBody className="space-y-5">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg bg-surface-2/70 px-3 py-2">
                  <div className="text-[11px] text-fg-3">{t('people.speakerDetail.meanAge')}</div>
                  <div className="text-lg font-semibold">{p.age_mean ? t('people.speakerDetail.years', Math.round(p.age_mean)) : '—'}</div>
                </div>
                <div className="rounded-lg bg-surface-2/70 px-3 py-2">
                  <div className="text-[11px] text-fg-3">{t('people.speakerDetail.ageGroup')}</div>
                  <div className="truncate text-lg font-semibold">{p.age_group ? ageGroupLabel(Object.keys(p.age_group)[0]) : '—'}</div>
                </div>
              </div>
              {p.sex && (
                <StackBar
                  format={(v) => `${Math.round(v * 100)}%`}
                  parts={[
                    { key: 'm', label: sexLabel.male, value: p.sex.male ?? 0, color: 'var(--series-1)' },
                    { key: 'f', label: sexLabel.female, value: p.sex.female ?? 0, color: 'var(--series-2)' },
                  ]}
                />
              )}
              {p.emotion && (
                <div>
                  <div className="mb-2 text-xs font-medium text-fg-3">{t('people.speakerDetail.emotion')}</div>
                  <HBars
                    rows={Object.entries(p.emotion)
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, v]) => ({ key: k, label: emotionLabel[k] ?? k, value: v }))}
                    format={(v) => `${Math.round(v * 100)}%`}
                  />
                </div>
              )}
              {p.voice_quality && (
                <div>
                  <div className="mb-2 text-xs font-medium text-fg-3">{t('people.speakerDetail.voiceQuality')}</div>
                  <HBars
                    rows={Object.entries(p.voice_quality)
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, v]) => ({ key: k, label: voiceQualityLabel[k] ?? k, value: v, color: 'var(--series-3)' }))}
                    format={(v) => `${Math.round(v * 100)}%`}
                    max={1}
                  />
                </div>
              )}
              <p className="text-[11px] leading-4 text-fg-3">
                <Badge tone="warning" className="mr-1 h-[18px] px-1.5 text-[10px]">
                  {t('people.speakerDetail.referenceOnly')}
                </Badge>
                {t('people.speakerDetail.biasNote')}
              </p>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title={t('people.speakerDetail.channels')} />
            <CardBody>
              <HBars
                rows={s.channels.map((c, i) => ({ key: c.id, label: c.title, value: c.total_sec, color: ORDINAL[Math.min(2, i)] }))}
                format={fmtHours}
              />
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  )
}
void ageGroupOrder
