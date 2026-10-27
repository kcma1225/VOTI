import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, ArrowRight, CheckCircle2, Film, Link2, ListVideo, Smartphone, Tv } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, useJobs } from '@/api/queries'
import type { ResolveResult } from '@/api/types'
import { classifyUrl } from '@/lib/url'
import { fmtInt, fmtRelative } from '@/lib/format'
import { jobKind } from '@/lib/labels'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Card, CardBody, CardHeader, Kbd } from '@/components/ui/primitives'
import { JobStatusBadge } from '@/components/domain/status'
import { cn } from '@/lib/cn'

const EXAMPLES = [
  { label: '頻道', url: 'https://www.youtube.com/@ptstaigi' },
  { label: '頻道影片分頁', url: 'https://www.youtube.com/@amaskitchen/videos' },
  { label: '播放清單', url: 'https://www.youtube.com/playlist?list=PLx9fQ0abcdEFG' },
  { label: '影片', url: 'https://youtu.be/dQw4w9WgXcQ' },
  { label: 'Shorts', url: 'https://www.youtube.com/shorts/aBcDeFgHiJk' },
]

function Classification({ url }: { url: string }) {
  const c = useMemo(() => classifyUrl(url), [url])
  if (c.kind === 'empty')
    return <p className="text-[13px] text-fg-3">貼上網址後，這裡會即時顯示辨識結果。</p>
  if (c.kind === 'invalid')
    return (
      <p className="flex items-center gap-2 text-[13px] text-danger-fg">
        <AlertCircle className="size-4 shrink-0" />
        {c.reason}
      </p>
    )
  const map = {
    channel: { icon: <Tv />, title: '頻道', body: `${c.kind === 'channel' ? c.handle : ''}　會列出所有影片與 Shorts（不會自動下載）`, tone: 'text-series-1' },
    playlist: { icon: <ListVideo />, title: '播放清單', body: c.kind === 'playlist' && c.withVideo ? '網址同時含影片與清單，將以整份清單為準' : '會列出清單中的所有影片', tone: 'text-series-7' },
    video: { icon: c.kind === 'video' && c.short ? <Smartphone /> : <Film />, title: c.kind === 'video' && c.short ? 'Shorts 短影片' : '單一影片', body: `影片 ID ${c.kind === 'video' ? c.id : ''}，會一併建立所屬頻道`, tone: 'text-series-3' },
  }[c.kind]
  return (
    <div className="flex min-w-0 items-start gap-3">
      <div className={cn('grid size-9 shrink-0 place-items-center rounded-lg bg-surface-2 [&_svg]:size-[18px]', map.tone)}>{map.icon}</div>
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
          <CheckCircle2 className="size-3.5 text-success" /> 偵測到：{map.title}
        </div>
        <div className="mt-0.5 truncate text-xs text-fg-3">{map.body}</div>
      </div>
    </div>
  )
}

function ResultCard({ r }: { r: ResolveResult }) {
  const to = r.channel ? `/channels/${r.channel.id}` : r.media ? `/media/${r.media.id}` : r.playlist?.channel_id ? `/channels/${r.playlist.channel_id}/playlists` : '/channels'
  const title = r.channel?.title ?? r.playlist?.title ?? r.media?.title ?? ''
  return (
    <Card className="mt-4 border-success/30 animate-slide-up">
      <CardBody className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <Avatar name={title} size={44} rounded="lg" />
        <div className="min-w-0 flex-1">
          <div className="text-xs text-fg-3">{r.type === 'channel' ? '已加入頻道' : r.type === 'playlist' ? '已加入播放清單' : '已加入影片'}</div>
          <div className="truncate text-[15px] font-semibold">{title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-3">
            <JobStatusBadge status={r.job.status} /> {jobKind[r.job.kind]}
            {r.channel && <span>· 目前已知 {fmtInt(r.channel.counts.listed)} 部影片</span>}
          </div>
        </div>
        <Button asChild variant="primary">
          <Link to={to}>
            前往篩選與下載 <ArrowRight />
          </Link>
        </Button>
      </CardBody>
    </Card>
  )
}

export default function AddSource() {
  const [url, setUrl] = useState('')
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
      <PageHeader title="新增來源" description="貼上 YouTube 頻道、播放清單或影片網址。系統只會先列出影片清單，下載前你可以依字幕、長度與日期篩選。" />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <Card>
            <form onSubmit={submit}>
              <CardBody className="space-y-4">
                <label htmlFor="url" className="text-[13px] font-medium">
                  YouTube 網址
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
                    加入並列出影片
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
                  <div className="mb-2 text-xs text-fg-3">範例（點選填入）</div>
                  <div className="flex flex-wrap gap-2">
                    {EXAMPLES.map((e) => (
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
            <CardHeader title="最近的列出工作" description="頻道與播放清單的列表會在背景更新" />
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
                <li className="px-5 py-6 text-center text-[13px] text-fg-3">尚無列出工作</li>
              )}
            </ul>
          </Card>
        </div>

        <aside className="space-y-4">
          <Card>
            <CardHeader title="接下來會發生什麼？" />
            <CardBody>
              <ol className="space-y-4 text-[13px]">
                {[
                  ['列出影片', '取得頻道的影片與 Shorts 清單（標題、長度、日期）。'],
                  ['取得詳細資訊', '逐部檢查字幕：原生（上傳者）字幕或 YouTube 自動字幕。'],
                  ['篩選與下載', '在頻道頁篩選後批次下載；預設只下載有原生字幕的影片。'],
                  ['自動處理', '音質分析 → 語者分段 → 切分音段 → 語者特徵 → 重複片頭偵測。'],
                ].map(([t, d], i) => (
                  <li key={t} className="flex gap-3">
                    <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary-soft text-xs font-semibold text-primary-soft-fg">{i + 1}</span>
                    <div>
                      <div className="font-medium">{t}</div>
                      <div className="mt-0.5 text-xs leading-5 text-fg-3">{d}</div>
                    </div>
                  </li>
                ))}
              </ol>
            </CardBody>
          </Card>
          <p className="px-1 text-xs leading-5 text-fg-3">
            提示：按 <Kbd>Enter</Kbd> 送出。下載速率與間隔可在「設定 → YouTube」調整，以避免被 YouTube 限流。
          </p>
        </aside>
      </div>
    </Page>
  )
}
