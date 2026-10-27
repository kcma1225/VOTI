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
  const L = c.counts.listed || 1
  const segs = [
    { k: 'ready', v: c.counts.ready, cls: 'bg-success', label: '可用' },
    { k: 'dl', v: Math.max(0, c.counts.downloaded - c.counts.ready), cls: 'bg-primary', label: '處理中' },
    { k: 'cc', v: Math.max(0, c.counts.with_manual_captions - c.counts.downloaded), cls: '', label: '有原生字幕未下載' },
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
            {c.handle} · {fmtCompact(c.subscriber_count)} 位訂閱者
          </div>
        </div>
        {c.list_status === 'listing' ? (
          <Badge tone="primary">
            <Loader2 className="size-3 animate-spin" /> 列出中
          </Badge>
        ) : c.list_status === 'failed' ? (
          <Badge tone="danger" dot>
            列出失敗
          </Badge>
        ) : null}
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2">
        {[
          ['影片', fmtInt(c.counts.listed)],
          ['原生字幕', fmtInt(c.counts.with_manual_captions)],
          ['可用語音', `${fmt1(c.hours.speech)} 時`],
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
              <Tooltip key={s.k} content={`${s.label}：${fmtInt(s.v)} 部`}>
                <span className={s.cls} style={{ width: `${(s.v / L) * 100}%`, background: s.k === 'cc' ? 'var(--seq-3)' : undefined }} />
              </Tooltip>
            ) : null,
          )}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-fg-3">
          <span className="flex items-center gap-1">
            <Captions className="size-3" /> {fmtInt(c.counts.detailed)} / {fmtInt(c.counts.listed)} 已檢查字幕
          </span>
          <span className="shrink-0">{c.last_listed_at ? `更新於${fmtRelative(c.last_listed_at)}` : '尚未列出'}</span>
        </div>
      </div>
    </Link>
  )
}

export default function Channels() {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('speech')
  const ch = useChannels(q || undefined)
  const items = useMemo(() => {
    const xs = [...(ch.data?.items ?? [])]
    if (sort === 'speech') xs.sort((a, b) => b.hours.speech - a.hours.speech)
    if (sort === 'videos') xs.sort((a, b) => b.counts.listed - a.counts.listed)
    if (sort === 'recent') xs.sort((a, b) => (b.last_listed_at ?? '').localeCompare(a.last_listed_at ?? ''))
    if (sort === 'title') xs.sort((a, b) => a.title.localeCompare(b.title, 'zh-TW'))
    return xs
  }, [ch.data, sort])

  return (
    <Page>
      <PageHeader
        title="頻道"
        description={ch.data ? `${fmtInt(ch.data.total)} 個頻道 · 點選頻道以篩選影片、批次下載與檢視語者` : '已加入的 YouTube 頻道'}
        actions={
          <Button asChild variant="primary">
            <Link to="/sources/new">
              <Plus /> 新增來源
            </Link>
          </Button>
        }
      />
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <SearchInput value={q} onChange={setQ} placeholder="搜尋頻道名稱或 @handle" className="sm:max-w-xs sm:flex-1" />
        <Select value={sort} onChange={(e) => setSort(e.target.value)} className="sm:w-44" aria-label="排序">
          <option value="speech">依可用語音時數</option>
          <option value="videos">依影片數</option>
          <option value="recent">依最近更新</option>
          <option value="title">依名稱</option>
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
            title={q ? '找不到符合的頻道' : '還沒有任何頻道'}
            description={q ? '試試其他關鍵字。' : '從貼上一個 YouTube 頻道網址開始。系統會列出所有影片，讓你挑選要下載的部分。'}
            action={
              !q && (
                <Button asChild variant="primary">
                  <Link to="/sources/new">
                    <Plus /> 新增來源
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
