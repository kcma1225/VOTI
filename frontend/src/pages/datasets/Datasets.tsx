import { Link } from 'react-router'
import { Database, Plus } from 'lucide-react'
import { useDatasets } from '@/api/queries'
import { fmtHours, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Card, Skeleton } from '@/components/ui/primitives'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { ExportRow, FilterTags } from './common'
import { useT } from '@/i18n'

export default function Datasets() {
  const ds = useDatasets()
  const t = useT()
  return (
    <Page>
      <PageHeader
        title={t('output.datasets.title')}
        description={t('output.datasets.description')}
        actions={
          <Button asChild variant="primary">
            <Link to="/datasets/new">
              <Plus /> {t('output.datasets.create')}
            </Link>
          </Button>
        }
      />
      {ds.isError ? (
        <Card>
          <ErrorState error={ds.error} onRetry={() => ds.refetch()} />
        </Card>
      ) : !ds.data ? (
        <div className="space-y-4">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-44 rounded-xl" />
          ))}
        </div>
      ) : !ds.data.length ? (
        <Card>
          <EmptyState
            icon={<Database />}
            title={t('output.datasets.emptyTitle')}
            description={t('output.datasets.emptyDesc')}
            action={
              <Button asChild variant="primary">
                <Link to="/datasets/new">
                  <Plus /> {t('output.datasets.create')}
                </Link>
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="space-y-4">
          {ds.data.map((d) => (
            <Card key={d.id} className="overflow-clip">
              <Link to={`/datasets/${d.id}`} className="group flex flex-col gap-4 p-4 hover:bg-surface-2/40 sm:flex-row sm:p-5">
                <div className="min-w-0 flex-1">
                  <h3 className="text-[15px] font-semibold group-hover:text-primary">{d.name}</h3>
                  {d.description && <p className="mt-0.5 text-[13px] leading-5 text-fg-3 break-cjk">{d.description}</p>}
                  <div className="mt-3">
                    <FilterTags f={d.filters} />
                  </div>
                  <div className="mt-3 text-xs text-fg-3">
                    {t('output.datasets.createdBy', d.created_by?.display_name ?? '—', fmtRelative(d.updated_at))}
                  </div>
                </div>
                <div className="flex shrink-0 gap-6 sm:flex-col sm:items-end sm:gap-1 sm:text-right">
                  <div>
                    <div className="text-2xl font-semibold leading-8">{d.total_sec != null ? fmtHours(d.total_sec) : '—'}</div>
                    <div className="text-xs text-fg-3">{t('output.datasets.matching')}</div>
                  </div>
                  <div className="text-[13px] tabular text-fg-2 sm:mt-1">{d.segment_count != null ? t('output.datasets.segments', d.segment_count) : ''}</div>
                </div>
              </Link>
              {d.last_export && (
                <div className="border-t border-border bg-surface-2/30">
                  <div className="px-4 pt-2.5 text-[11px] font-medium text-fg-3 sm:px-5">{t('output.datasets.lastExport')}</div>
                  <ExportRow e={d.last_export} />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </Page>
  )
}
