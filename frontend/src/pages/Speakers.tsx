import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { GitMerge, History, Users } from 'lucide-react'
import { api } from '@/api/client'
import { onErr, useChannels, useClusterRuns, useSpeakers } from '@/api/queries'
import { fmtDateTime, fmtInt, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody } from '@/components/ui/primitives'
import { Chip, Field, SearchInput, Select, Slider } from '@/components/ui/form'
import { Dialog, Popover } from '@/components/ui/overlay'
import { Pagination } from '@/components/ui/data-table'
import { EmptyState, ErrorState, ListSkeleton } from '@/components/ui/states'
import { SpeakerList } from '@/components/domain/speakers'

const LIMIT = 50

function ClusterDialog({ open, onOpenChange, current }: { open: boolean; onOpenChange: (o: boolean) => void; current?: number }) {
  const [th, setTh] = useState(current ?? 0.6)
  const [scope, setScope] = useState<string[]>([])
  const ch = useChannels()
  const qc = useQueryClient()
  const run = useMutation({
    mutationFn: () => api.cluster(th, scope),
    onSuccess: () => {
      toast.success('已開始語者分群', { description: '完成後會通知你；舊的分群結果會保留在歷史紀錄。' })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      onOpenChange(false)
    },
    onError: onErr,
  })
  const desc = th < 0.6 ? '寬鬆：較少群，不同人可能被併在一起' : th > 0.82 ? '嚴格：較多群，同一人可能被拆成多群' : '平衡：建議的預設範圍'
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="執行語者分群"
      description="以 ECAPA 語者嵌入做階層式分群。人工指定的語者會被保留，不受重新分群影響。"
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>取消</Button>
          <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}>
            <GitMerge /> 開始分群
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        <Field label="相似度門檻" aside={th.toFixed(2)} hint={desc}>
          <Slider aria-label="相似度門檻" value={[th]} onValueChange={([v]) => setTh(v)} min={0.3} max={0.9} step={0.01} />
          <div className="flex justify-between text-[11px] text-fg-3">
            <span>0.40 · 較少群</span>
            <span>0.95 · 較多群</span>
          </div>
        </Field>
        <Field label="範圍" hint="不選代表所有頻道一起分群（可找出跨頻道的同一位語者）。">
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
  const runs = useClusterRuns()
  const ch = useChannels()
  const cur = runs.data?.find((r) => r.is_current)
  const sp = useSpeakers({ q: q || undefined, channel_id: channel || undefined, sort, limit: LIMIT, offset })

  return (
    <Page>
      <PageHeader
        title="語者"
        description="候選語者是分群結果，不等於確認的真實人物。為重要語者命名並標記為已確認，資料集就能依語者設定上限。"
        actions={
          <Button variant="primary" onClick={() => setOpen(true)}>
            <GitMerge /> 執行分群
          </Button>
        }
      />

      <Card className="mb-4">
        <CardBody className="flex flex-col gap-4 py-4 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[13px] font-medium">目前的分群結果</span>
              {cur && <Badge tone="success" dot>使用中</Badge>}
            </div>
            <div className="mt-1 text-[13px] text-fg-3">
              {cur ? (
                <>
                  門檻 <span className="font-medium tabular text-fg">{cur.threshold.toFixed(2)}</span> · {fmtInt(cur.stats.speakers ?? 0)} 群 · {fmtInt(cur.stats.segments ?? 0)} 個音段 ·{' '}
                  {cur.scope.channel_ids?.length ? `${cur.scope.channel_ids.length} 個頻道` : '全部頻道'} · {fmtRelative(cur.created_at)}
                </>
              ) : (
                '尚未執行分群'
              )}
            </div>
          </div>
          {runs.data && runs.data.length > 1 && (
            <Popover
              align="end"
              trigger={
                <Button size="sm" variant="ghost">
                  <History /> 歷史紀錄（{runs.data.length}）
                </Button>
              }
            >
              <div className="mb-2 text-[13px] font-semibold">分群歷史</div>
              <ul className="divide-y divide-border">
                {runs.data.map((r) => (
                  <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                    <span>
                      門檻 {r.threshold.toFixed(2)} · {fmtInt(r.stats.speakers ?? 0)} 群
                      <span className="block text-[11px] text-fg-3">{fmtDateTime(r.created_at)}</span>
                    </span>
                    {r.is_current && <Badge tone="success">使用中</Badge>}
                  </li>
                ))}
              </ul>
            </Popover>
          )}
        </CardBody>
      </Card>

      <div className="mb-3 flex flex-col gap-2 sm:flex-row">
        <SearchInput value={q} onChange={(v) => (setQ(v), setOffset(0))} placeholder="搜尋名稱或 cand_ 標籤" className="sm:max-w-xs sm:flex-1" />
        <Select value={channel} onChange={(e) => (setChannel(e.target.value), setOffset(0))} className="sm:w-48" aria-label="頻道">
          <option value="">所有頻道</option>
          {ch.data?.items.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </Select>
        <Select value={sort} onChange={(e) => setSort(e.target.value)} className="sm:w-40" aria-label="排序">
          <option value="-total_sec">依時數</option>
          <option value="-segment_count">依音段數</option>
          <option value="label">依標籤</option>
        </Select>
      </div>

      <Card className="overflow-clip">
        {sp.isError ? (
          <ErrorState error={sp.error} onRetry={() => sp.refetch()} />
        ) : !sp.data ? (
          <ListSkeleton rows={8} />
        ) : !sp.data.items.length ? (
          <EmptyState icon={<Users />} title="沒有符合的語者" description="影片處理完成後執行分群，就會出現候選語者。" />
        ) : (
          <>
            <SpeakerList items={sp.data.items} />
            <Pagination total={sp.data.total} limit={LIMIT} offset={offset} onChange={setOffset} className="border-t border-border" />
          </>
        )}
      </Card>
      <ClusterDialog open={open} onOpenChange={setOpen} current={cur?.threshold} />
    </Page>
  )
}
