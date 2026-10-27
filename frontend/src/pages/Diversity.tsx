import { useState } from 'react'
import { Link } from 'react-router'
import { BarChart3 } from 'lucide-react'
import { useChannels, useDiversity } from '@/api/queries'
import { fmt1, fmtHours, fmtInt, fmtPct, hoursUnit } from '@/lib/format'
import { ageGroupLabel, ageGroupOrder, emotionLabel, sexLabel } from '@/lib/labels'
import { cn } from '@/lib/cn'
import { useT } from '@/i18n'
import { Page, PageHeader } from '@/components/layout/page'
import { Card, CardHeader, Stat } from '@/components/ui/primitives'
import { Select } from '@/components/ui/form'
import { CardsSkeleton, EmptyState, ErrorState } from '@/components/ui/states'
import { ChartCard, HBars, Lorenz, ORDINAL, RankColumns, StackBar } from '@/components/charts/charts'

export default function Diversity() {
  const [channel, setChannel] = useState('')
  const ch = useChannels()
  const t = useT()
  const dq = useDiversity(channel || undefined)
  const d = dq.data

  return (
    <Page>
      <PageHeader
        title={t('people.diversity.title')}
        description={t('people.diversity.description')}
        actions={
          <Select value={channel} onChange={(e) => setChannel(e.target.value)} className="w-full sm:w-56" aria-label={t('people.diversity.scope')}>
            <option value="">{t('people.diversity.allChannels')}</option>
            {ch.data?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        }
      />
      {dq.isError ? (
        <Card>
          <ErrorState error={dq.error} onRetry={() => dq.refetch()} />
        </Card>
      ) : !d ? (
        <CardsSkeleton />
      ) : !d.speaker_hours.length ? (
        <Card>
          <EmptyState icon={<BarChart3 />} title={t('people.diversity.emptyTitle')} description={t('people.diversity.emptyDesc')} />
        </Card>
      ) : (
        <div className={cn('space-y-4 transition-opacity', dq.isFetching && 'opacity-60')}>
          {(() => {
            const sorted = [...d.speaker_hours].sort((a, b) => b.total_sec - a.total_sec)
            const total = sorted.reduce((a, s) => a + s.total_sec, 0)
            const top = Math.max(1, Math.round(sorted.length * 0.1))
            const topShare = sorted.slice(0, top).reduce((a, s) => a + s.total_sec, 0) / (total || 1)
            const giniWord = d.gini < 0.4 ? t('people.diversity.giniLow') : d.gini < 0.6 ? t('people.diversity.giniMid') : t('people.diversity.giniHigh')
            return (
              <>
                <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                  <Card className="p-4 sm:p-5">
                    <Stat label={t('people.diversity.candidates')} value={fmtInt(sorted.length)} unit={t('people.diversity.clustersUnit')} hint={t('people.diversity.latestRun')} />
                  </Card>
                  <Card className="p-4 sm:p-5">
                    <Stat label={t('people.diversity.totalHours')} value={fmt1(total / 3600)} unit={hoursUnit()} />
                  </Card>
                  <Card className="p-4 sm:p-5">
                    <Stat label={t('people.diversity.gini')} value={d.gini.toFixed(2)} hint={t('people.diversity.giniHint', giniWord)} />
                  </Card>
                  <Card className="p-4 sm:p-5">
                    <Stat label={t('people.diversity.topShare', top)} value={fmtPct(topShare)} hint={t('people.diversity.topShareHint')} />
                  </Card>
                </div>

                <div className="grid gap-4 lg:grid-cols-3">
                  <ChartCard
                    title={t('people.diversity.sexTitle')}
                    className="lg:col-span-1"
                    table={{ columns: [t('people.diversity.sexCol'), t('people.diversity.hoursCol')], rows: Object.entries(d.by_sex).map(([k, v]) => [sexLabel[k] ?? k, +(v / 3600).toFixed(2)]) }}
                  >
                    {(() => {
                      const m = d.by_sex.male ?? 0
                      const fe = d.by_sex.female ?? 0
                      const share = m + fe ? fe / (m + fe) : 0
                      return (
                        <div className="mb-4">
                          <div className="flex items-baseline gap-2">
                            <span className="text-3xl font-semibold">{fmtPct(share)}</span>
                            <span className="text-[13px] text-fg-3">{t('people.diversity.femaleShare')}</span>
                          </div>
                          <p className="mt-1 text-xs text-fg-3">
                            {Math.abs(share - 0.5) < 0.05 ? t('people.diversity.balanced') : share < 0.5 ? t('people.diversity.fewerFemale', Math.round((0.5 - share) * 100)) : t('people.diversity.moreFemale', Math.round((share - 0.5) * 100))}
                          </p>
                        </div>
                      )
                    })()}
                    <StackBar
                      height={18}
                      format={fmtHours}
                      parts={[
                        { key: 'male', label: sexLabel.male, value: d.by_sex.male ?? 0, color: 'var(--series-1)' },
                        { key: 'female', label: sexLabel.female, value: d.by_sex.female ?? 0, color: 'var(--series-2)' },
                        { key: 'unknown', label: sexLabel.unknown, value: d.by_sex.unknown ?? 0, color: 'var(--series-muted)' },
                      ]}
                    />
                  </ChartCard>
                  <ChartCard
                    title={t('people.diversity.ageTitle')}
                    table={{ columns: [t('people.diversity.ageCol'), t('people.diversity.hoursCol')], rows: [...ageGroupOrder, 'unknown'].filter((g) => d.by_age_group[g]).map((g) => [ageGroupLabel(g), +(d.by_age_group[g] / 3600).toFixed(2)]) }}
                  >
                    <HBars
                      rows={[...ageGroupOrder, 'unknown']
                        .filter((g) => d.by_age_group[g])
                        .map((g, i) => ({ key: g, label: ageGroupLabel(g), value: d.by_age_group[g], color: g === 'unknown' ? 'var(--series-muted)' : ORDINAL[Math.min(2, Math.floor(i / 2))] }))}
                      format={fmtHours}
                    />
                  </ChartCard>
                  <ChartCard
                    title={t('people.diversity.emotionTitle')}
                    table={{ columns: [t('people.diversity.emotionCol'), t('people.diversity.hoursCol')], rows: Object.entries(d.by_emotion).map(([k, v]) => [emotionLabel[k] ?? k, +(v / 3600).toFixed(2)]) }}
                  >
                    <HBars
                      rows={Object.entries(d.by_emotion)
                        .sort((a, b) => b[1] - a[1])
                        .map(([k, v]) => ({ key: k, label: emotionLabel[k] ?? (k === 'unknown' ? t('people.diversity.unknown') : k), value: v, color: k === 'unknown' ? 'var(--series-muted)' : 'var(--series-1)' }))}
                      format={fmtHours}
                    />
                  </ChartCard>
                </div>

                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                  <ChartCard
                    title={t('people.diversity.longTail')}
                    description={t('people.diversity.longTailDesc', top)}
                    table={{ columns: [t('people.diversity.speakerCol'), t('people.diversity.hoursCol')], rows: sorted.map((s) => [s.label, +(s.total_sec / 3600).toFixed(2)]) }}
                  >
                    <RankColumns values={sorted.map((s) => s.total_sec / 3600)} labels={sorted.map((s) => s.label)} format={(v) => t('people.diversity.hoursShort', fmt1(v))} highlightTop={top} height={220} />
                  </ChartCard>
                  <ChartCard title={t('people.diversity.lorenz')} description={t('people.diversity.lorenzDesc', d.gini.toFixed(2))}>
                    <Lorenz values={sorted.map((s) => s.total_sec)} height={220} />
                    <p className="mt-2 text-[11px] text-fg-3">{t('people.diversity.lorenzAxes')}</p>
                  </ChartCard>
                </div>

                <Card className="overflow-clip">
                  <CardHeader title={t('people.diversity.byChannel')} description={t('people.diversity.byChannelDesc')} />
                  <div className="mt-3 overflow-x-auto border-t border-border">
                    <table className="w-full min-w-[520px] text-[13px]">
                      <thead className="bg-surface-2/70 text-xs text-fg-3">
                        <tr>
                          <th className="px-4 py-2 text-left font-medium sm:px-5">{t('people.diversity.colChannel')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('people.diversity.colSpeakers')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('people.diversity.colHours')}</th>
                          <th className="w-[40%] px-4 py-2 text-left font-medium sm:px-5">{t('people.diversity.colTopShare')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {d.by_channel.map((c) => (
                          <tr key={c.channel_id}>
                            <td className="max-w-0 px-4 py-2.5 sm:px-5">
                              <Link to={`/channels/${c.channel_id}/speakers`} className="block truncate font-medium hover:text-primary">
                                {c.title}
                              </Link>
                            </td>
                            <td className="px-3 py-2.5 text-right tabular">{fmtInt(c.speakers)}</td>
                            <td className="whitespace-nowrap px-3 py-2.5 text-right tabular">{fmtHours(c.total_sec)}</td>
                            <td className="px-4 py-2.5 sm:px-5">
                              <div className="flex items-center gap-3">
                                <div className="h-2 min-w-0 flex-1 rounded-full bg-surface-3">
                                  <div className={cn('h-full rounded-full', c.top_speaker_share > 0.5 ? 'bg-warning' : 'bg-series-1')} style={{ width: `${c.top_speaker_share * 100}%` }} />
                                </div>
                                <span className="w-10 text-right tabular text-fg-2">{fmtPct(c.top_speaker_share)}</span>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </>
            )
          })()}
        </div>
      )}
    </Page>
  )
}
