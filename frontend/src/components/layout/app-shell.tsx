import { Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  ChevronsLeft,
  ChevronsRight,
  KeyRound,
  LogOut,
  Menu as MenuIcon,
  Monitor,
  Moon,
  MoreHorizontal,
  Sun,
} from 'lucide-react'
import { api } from '@/api/client'
import { qk, useJobs, useMe } from '@/api/queries'
import type { Job, User } from '@/api/types'
import { cn } from '@/lib/cn'
import { jobKind } from '@/lib/labels'
import { useIsDesktop } from '@/hooks/useMedia'
import { useTheme, type ThemePref } from '@/hooks/useTheme'
import { Avatar, Progress, Skeleton } from '@/components/ui/primitives'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator, MenuTrigger, Sheet, Tooltip } from '@/components/ui/overlay'
import { Button } from '@/components/ui/button'
import { PillTabs } from '@/components/ui/tabs'
import { EmptyState } from '@/components/ui/states'
import { JobRow } from '@/components/domain/job-row'
import { navGroups, phoneTabs, type NavItem } from './nav'

/* ───────────── brand ───────────── */
function BrandMark({ className }: { className?: string }) {
  return (
    <div className={cn('grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-fg shadow-sm', className)}>
      <svg viewBox="0 0 24 24" className="size-[18px]" fill="currentColor" aria-hidden>
        <rect x="3" y="9.5" width="2.6" height="5" rx="1.3" />
        <rect x="7.6" y="6.5" width="2.6" height="11" rx="1.3" />
        <rect x="12.2" y="3.5" width="2.6" height="17" rx="1.3" />
        <rect x="16.8" y="8" width="2.6" height="8" rx="1.3" />
      </svg>
    </div>
  )
}

/* ───────────── jobs: watcher (toasts) + activity summary ───────────── */
const ACTIVE = ['queued', 'running']
function useJobsWatcher() {
  const recent = useJobs({ limit: 40 }, 3000)
  const prev = useRef<Map<string, string> | null>(null)
  const qc = useQueryClient()
  const navigate = useNavigate()
  useEffect(() => {
    const items = recent.data?.items
    if (!items) return
    const next = new Map(items.map((j) => [j.id, j.status]))
    if (prev.current) {
      for (const j of items) {
        const before = prev.current.get(j.id)
        if (before && ACTIVE.includes(before) && !ACTIVE.includes(j.status)) notify(j)
      }
    }
    prev.current = next
    function notify(j: Job) {
      const label = jobKind[j.kind] ?? j.kind
      const subject = j.media_title ?? j.channel_title ?? ''
      const go = j.media_id ? `/media/${j.media_id}` : j.channel_id ? `/channels/${j.channel_id}` : '/jobs'
      if (j.status === 'completed') {
        toast.success(`${label}完成`, { description: subject, action: { label: '查看', onClick: () => navigate(go) } })
        if (j.kind === 'dataset.export') qc.invalidateQueries({ queryKey: ['exports'] })
        if (j.kind === 'speakers.cluster') qc.invalidateQueries({ queryKey: ['speakers'] })
        if (j.media_id) qc.invalidateQueries({ queryKey: ['mediaDetail', j.media_id] })
      } else if (j.status === 'failed') {
        toast.error(`${label}失敗`, { description: j.error_message ?? subject, action: { label: '查看', onClick: () => navigate('/jobs?status=failed') } })
      }
    }
  }, [recent.data, navigate, qc])
  return recent
}

function ActivityButton({ collapsed, onOpen }: { collapsed?: boolean; onOpen: () => void }) {
  const active = useJobs({ status: ['running', 'queued'], limit: 20 }, 3000)
  const running = active.data?.counts.running ?? 0
  const queued = active.data?.counts.queued ?? 0
  const failed = active.data?.counts.failed ?? 0
  const top = active.data?.items.find((j) => j.status === 'running')
  const btn = (
    <button
      onClick={onOpen}
      className={cn(
        'group flex w-full items-center gap-3 rounded-lg border border-border bg-surface text-left shadow-xs transition-colors hover:border-border-strong',
        collapsed ? 'size-11 justify-center p-0' : 'p-2.5',
      )}
      aria-label="工作佇列"
    >
      <span className="relative grid size-6 shrink-0 place-items-center">
        <Activity className={cn('size-4', running ? 'text-primary' : 'text-fg-3')} />
        {running > 0 && <span className="absolute -right-0.5 -top-0.5 size-2 animate-pulse-dot rounded-full bg-primary text-primary" />}
        {!running && failed > 0 && <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-danger" />}
      </span>
      {!collapsed && (
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2 text-[13px] font-medium text-fg">
            <span>工作</span>
            <span className="text-xs font-normal tabular text-fg-3">
              {running} 執行 · {queued} 排隊
            </span>
          </span>
          {top ? (
            <span className="mt-1.5 block">
              <Progress value={top.progress} className="h-1" />
              <span className="mt-1 block truncate text-[11px] text-fg-3">
                {jobKind[top.kind]}
                {top.media_title ? `：${top.media_title}` : ''}
              </span>
            </span>
          ) : (
            <span className="mt-0.5 block text-[11px] text-fg-3">{failed ? `${failed} 個失敗待處理` : '佇列閒置中'}</span>
          )}
        </span>
      )}
    </button>
  )
  return collapsed ? (
    <Tooltip content={`工作：${running} 執行中、${queued} 排隊`} side="right">
      {btn}
    </Tooltip>
  ) : (
    btn
  )
}

export function JobsDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [tab, setTab] = useState<'active' | 'failed' | 'done'>('active')
  const status = tab === 'active' ? ['running', 'queued'] : tab === 'failed' ? ['failed', 'blocked', 'interrupted'] : ['completed', 'cancelled']
  const q = useJobs({ status, limit: 50 }, open ? 2500 : 0)
  const counts = q.data?.counts ?? {}
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="工作佇列"
      description="即時更新 · 每 2.5 秒"
      headerActions={
        <Button asChild size="sm" variant="ghost">
          <Link to="/jobs" onClick={() => onOpenChange(false)}>
            全部
          </Link>
        </Button>
      }
    >
      <div className="sticky top-0 z-10 border-b border-border bg-surface px-3 py-2">
        <PillTabs
          value={tab}
          onChange={setTab}
          items={[
            { value: 'active', label: '進行中', count: (counts.running ?? 0) + (counts.queued ?? 0) },
            { value: 'failed', label: '失敗', count: (counts.failed ?? 0) + (counts.blocked ?? 0) + (counts.interrupted ?? 0) },
            { value: 'done', label: '已完成' },
          ]}
        />
      </div>
      {q.isLoading ? (
        <div className="space-y-4 p-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : q.data?.items.length ? (
        <div className="divide-y divide-border">
          {q.data.items.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
        </div>
      ) : (
        <EmptyState compact icon={<Activity />} title={tab === 'failed' ? '沒有失敗的工作' : '目前沒有工作'} description="新增來源或下載影片後，處理進度會即時出現在這裡。" />
      )}
    </Sheet>
  )
}

/* ───────────── theme + user ───────────── */
const themeIcon: Record<ThemePref, ReactNode> = { system: <Monitor />, light: <Sun />, dark: <Moon /> }
function ThemeMenu({ collapsed }: { collapsed?: boolean }) {
  const { pref, setPref } = useTheme()
  return (
    <Menu>
      <Tooltip content="外觀主題" side={collapsed ? 'right' : 'top'}>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="外觀主題">
            {themeIcon[pref]}
          </Button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align={collapsed ? 'start' : 'end'}>
        <MenuLabel>外觀</MenuLabel>
        <MenuRadioGroup value={pref} onValueChange={(v) => setPref(v as ThemePref)}>
          <MenuRadioItem value="system" icon={<Monitor />}>跟隨系統</MenuRadioItem>
          <MenuRadioItem value="light" icon={<Sun />}>淺色</MenuRadioItem>
          <MenuRadioItem value="dark" icon={<Moon />}>深色</MenuRadioItem>
        </MenuRadioGroup>
      </MenuContent>
    </Menu>
  )
}

function UserMenu({ user, collapsed }: { user: User; collapsed?: boolean }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const logout = async () => {
    await api.logout().catch(() => undefined)
    qc.setQueryData(qk.me, null)
    qc.clear()
    navigate('/login')
  }
  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          className={cn(
            'flex min-w-0 items-center gap-2.5 rounded-lg text-left hover:bg-surface-2',
            collapsed ? 'size-11 justify-center' : 'h-11 flex-1 px-1.5',
          )}
          aria-label="帳號選單"
        >
          <Avatar name={user.display_name || user.username} size={30} />
          {!collapsed && (
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium leading-4 text-fg">{user.display_name || user.username}</span>
              <span className="block truncate text-[11px] leading-4 text-fg-3">{user.role === 'admin' ? '管理員' : '標註員'}</span>
            </span>
          )}
        </button>
      </MenuTrigger>
      <MenuContent align={collapsed ? 'start' : 'end'}>
        <MenuLabel>
          {user.display_name}（@{user.username}）
        </MenuLabel>
        <MenuItem icon={<KeyRound />} onSelect={() => navigate('/settings/account')}>
          變更密碼
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<LogOut />} danger onSelect={logout}>
          登出
        </MenuItem>
      </MenuContent>
    </Menu>
  )
}

/* ───────────── sidebar ───────────── */
function NavItemLink({ item, collapsed, onNavigate }: { item: NavItem; collapsed?: boolean; onNavigate?: () => void }) {
  const Icon = item.icon
  const link = (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'group relative flex items-center gap-3 rounded-lg text-sm font-medium transition-colors',
          collapsed ? 'size-11 justify-center' : 'h-10 px-3 sm:h-9',
          isActive ? 'bg-primary-soft text-primary-soft-fg' : 'text-fg-2 hover:bg-surface-2 hover:text-fg',
        )
      }
    >
      <Icon className="size-[18px] shrink-0" strokeWidth={1.9} />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </NavLink>
  )
  return collapsed ? (
    <Tooltip content={item.label} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  )
}

function NavList({ user, collapsed, onNavigate }: { user: User; collapsed?: boolean; onNavigate?: () => void }) {
  return (
    <nav className={cn('flex flex-col', collapsed ? 'items-center gap-1' : 'gap-4')} aria-label="主選單">
      {navGroups.map((g, gi) => {
        const items = g.items.filter((i) => !i.adminOnly || user.role === 'admin')
        if (!items.length) return null
        return (
          <div key={gi} className={cn('flex flex-col gap-0.5', collapsed && 'items-center')}>
            {g.label && !collapsed && <div className="px-3 pb-1 text-[11px] font-medium tracking-wide text-fg-3">{g.label}</div>}
            {g.label && collapsed && gi > 0 && <div className="my-1.5 h-px w-6 bg-border" />}
            {items.map((i) => (
              <NavItemLink key={i.to} item={i} collapsed={collapsed} onNavigate={onNavigate} />
            ))}
          </div>
        )
      })}
    </nav>
  )
}

function Sidebar({ user, collapsed, onToggle, onOpenJobs }: { user: User; collapsed: boolean; onToggle: () => void; onOpenJobs: () => void }) {
  return (
    <div
      className={cn(
        'relative z-30 hidden shrink-0 border-r border-border bg-surface/80 md:block',
        'transition-[width] duration-200',
        collapsed ? 'w-[72px]' : 'w-60',
      )}
    >
    <aside className="sticky top-0 flex h-dvh flex-col">
      <div className={cn('flex h-16 shrink-0 items-center gap-2.5', collapsed ? 'justify-center' : 'px-4')}>
        <Link to="/" className="flex min-w-0 items-center gap-2.5" aria-label="VOTI 首頁">
          <BrandMark />
          {!collapsed && (
            <span className="min-w-0">
              <span className="block text-[15px] font-semibold leading-5 tracking-tight">VOTI</span>
              <span className="block truncate text-[11px] leading-4 text-fg-3">台語語料工作台</span>
            </span>
          )}
        </Link>
      </div>
      <div className={cn('min-h-0 flex-1 overflow-y-auto scrollbar-thin pb-4', collapsed ? 'px-2' : 'px-3')}>
        <NavList user={user} collapsed={collapsed} />
      </div>
      <div className={cn('shrink-0 space-y-2 border-t border-border py-3', collapsed ? 'flex flex-col items-center px-2' : 'px-3')}>
        <ActivityButton collapsed={collapsed} onOpen={onOpenJobs} />
        <div className={cn('flex items-center gap-1', collapsed && 'flex-col')}>
          <UserMenu user={user} collapsed={collapsed} />
          <ThemeMenu collapsed={collapsed} />
          <Tooltip content={collapsed ? '展開側欄' : '收合側欄'} side="right">
            <Button variant="ghost" size="icon" onClick={onToggle} aria-label={collapsed ? '展開側欄' : '收合側欄'}>
              {collapsed ? <ChevronsRight /> : <ChevronsLeft />}
            </Button>
          </Tooltip>
        </div>
      </div>
    </aside>
    </div>
  )
}

/* ───────────── phone chrome ───────────── */
function PhoneTopBar({ user, onMenu, onOpenJobs }: { user: User; onMenu: () => void; onOpenJobs: () => void }) {
  const active = useJobs({ status: ['running', 'queued'], limit: 1 }, 3000)
  const running = active.data?.counts.running ?? 0
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-surface/85 px-2 backdrop-blur-xl md:hidden">
      <Button variant="ghost" size="icon" onClick={onMenu} aria-label="開啟選單">
        <MenuIcon />
      </Button>
      <Link to="/" className="flex min-w-0 flex-1 items-center gap-2">
        <BrandMark className="size-7" />
        <span className="truncate text-[15px] font-semibold tracking-tight">VOTI</span>
      </Link>
      <Button variant="ghost" size="icon" onClick={onOpenJobs} aria-label="工作佇列" className="relative">
        <Activity className={running ? 'text-primary' : undefined} />
        {running > 0 && (
          <span className="absolute right-1 top-1 grid h-4 min-w-4 place-items-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-fg">
            {running}
          </span>
        )}
      </Button>
      <ThemeMenu />
      <UserMenu user={user} collapsed />
    </header>
  )
}

function BottomTabs({ onMore }: { onMore: () => void }) {
  return (
    <nav
      className="sticky bottom-0 z-30 border-t border-border bg-surface/90 backdrop-blur-xl pb-safe md:hidden"
      aria-label="主要分頁"
    >
      <div className="grid h-16 grid-cols-5">
        {phoneTabs.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              cn('flex flex-col items-center justify-center gap-1 text-[11px] font-medium', isActive ? 'text-primary' : 'text-fg-3')
            }
          >
            <t.icon className="size-[22px]" strokeWidth={1.8} />
            {t.label}
          </NavLink>
        ))}
        <button onClick={onMore} className="flex flex-col items-center justify-center gap-1 text-[11px] font-medium text-fg-3">
          <MoreHorizontal className="size-[22px]" strokeWidth={1.8} />
          更多
        </button>
      </div>
    </nav>
  )
}

/* ───────────── shell ───────────── */
export function AppShell() {
  const me = useMe()
  const user = me.data
  const isDesktop = useIsDesktop()
  const [collapsed, setCollapsed] = useState(!isDesktop)
  const [drawer, setDrawer] = useState(false)
  const [jobsOpen, setJobsOpen] = useState(false)
  const location = useLocation()
  useJobsWatcher()

  useEffect(() => setCollapsed(!isDesktop), [isDesktop])
  useEffect(() => {
    setDrawer(false)
    window.scrollTo({ top: 0 })
  }, [location.pathname])

  if (!user) return null
  return (
    <div className="flex min-h-dvh">
      <Sidebar user={user} collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} onOpenJobs={() => setJobsOpen(true)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <PhoneTopBar user={user} onMenu={() => setDrawer(true)} onOpenJobs={() => setJobsOpen(true)} />
        <div className="min-w-0 flex-1">
          <Suspense fallback={<PageFallback />}>
            <Outlet />
          </Suspense>
        </div>
        <BottomTabs onMore={() => setDrawer(true)} />
      </div>
      <Sheet open={drawer} onOpenChange={setDrawer} side="left" title="VOTI" description="台語語料工作台">
        <div className="p-3">
          <NavList user={user} onNavigate={() => setDrawer(false)} />
        </div>
      </Sheet>
      <JobsDrawer open={jobsOpen} onOpenChange={setJobsOpen} />
    </div>
  )
}

export function PageFallback() {
  return (
    <div className="mx-auto w-full max-w-[1440px] space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <Skeleton className="h-8 w-48" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-72 rounded-xl" />
    </div>
  )
}
