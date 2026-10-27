import { useSearchParams } from 'react-router'
import { ListChecks } from 'lucide-react'
import { useJobs } from '@/api/queries'
import type { JobKind } from '@/api/types'
import { jobKind } from '@/lib/labels'
import { fmtInt } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Card, Skeleton } from '@/components/ui/primitives'
import { Select } from '@/components/ui/form'
import { PillTabs } from '@/components/ui/tabs'
import { Pagination } from '@/components/ui/data-table'
import { EmptyState, ErrorState } from '@/components/ui/states'
import { JobRow } from '@/components/domain/job-row'

const LIMIT = 30
const TABS = {
  active: ['running', 'queued'],
  failed: ['failed', 'blocked', 'interrupted'],
  done: ['completed', 'cancelled'],
  all: [],
} as const

export default function Jobs() {
  const [sp, setSp] = useSearchParams()
  const raw = sp.get('status') ?? 'active'
  const tab = (raw === 'failed' ? 'failed' : raw in TABS ? raw : 'active') as keyof typeof TABS
  const kind = sp.get('kind') ?? ''
  const offset = Number(sp.get('offset') ?? 0)
  const q = useJobs({ status: [...TABS[tab]], kind: kind ? [kind] : undefined, limit: LIMIT, offset }, 2500)
  const c = q.data?.counts ?? {}
  const set = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp)
    if (v) n.set(k, v)
    else n.delete(k)
    if (k !== 'offset') n.delete('offset')
    setSp(n, { replace: true })
  }
  return (
    <Page>
      <PageHeader title="工作佇列" description="所有背景工作：列出、下載、音訊處理、模型推論與匯出。每 2.5 秒自動更新。" />
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <PillTabs
          value={tab}
          onChange={(v) => set('status', v)}
          items={[
            { value: 'active', label: '進行中', count: (c.running ?? 0) + (c.queued ?? 0) },
            { value: 'failed', label: '需要處理', count: (c.failed ?? 0) + (c.blocked ?? 0) + (c.interrupted ?? 0) },
            { value: 'done', label: '已完成', count: (c.completed ?? 0) + (c.cancelled ?? 0) },
            { value: 'all', label: '全部' },
          ]}
        />
        <Select value={kind} onChange={(e) => set('kind', e.target.value)} className="sm:w-48" aria-label="工作類型" size_="sm">
          <option value="">所有類型</option>
          {(Object.keys(jobKind) as JobKind[]).map((k) => (
            <option key={k} value={k}>
              {jobKind[k]}
            </option>
          ))}
        </Select>
      </div>
      <Card className="overflow-clip">
        {q.isError ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : !q.data ? (
          <div className="space-y-4 p-5">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : !q.data.items.length ? (
          <EmptyState icon={<ListChecks />} title={tab === 'failed' ? '沒有需要處理的工作' : '沒有工作'} description={tab === 'active' ? '佇列目前是空的。' : undefined} />
        ) : (
          <>
            <div className="grid divide-y divide-border lg:grid-cols-2 lg:divide-y-0 [&>*]:border-border lg:[&>*:nth-child(odd)]:border-r lg:[&>*:nth-child(n+3)]:border-t">
              {q.data.items.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </div>
            <Pagination total={q.data.total} limit={LIMIT} offset={offset} onChange={(o) => set('offset', String(o))} className="border-t border-border" />
          </>
        )}
      </Card>
      {q.data && <p className="mt-3 text-center text-xs text-fg-3">共 {fmtInt(Object.values(c).reduce((a, b) => a + (b ?? 0), 0))} 筆工作紀錄</p>}
    </Page>
  )
}
