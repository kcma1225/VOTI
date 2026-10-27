import { Suspense, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  ChevronsLeft,
  ChevronsRight,
  KeyRound,
  Languages,
  LogOut,
  Menu as MenuIcon,
  Monitor,
  Moon,
  MoreHorizontal,
  Sun,
  UserRound,
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
import { BrandMark } from '@/components/brand/logo'
import { LANGS, msgs, t, useI18n, type Lang } from '@/i18n'

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
        toast.success(t('shell.jobs.toastDone', label), { description: subject, action: { label: t('common.view'), onClick: () => navigate(go) } })
        if (j.kind === 'dataset.export') qc.invalidateQueries({ queryKey: ['exports'] })
        if (j.kind === 'speakers.cluster') qc.invalidateQueries({ queryKey: ['speakers'] })
        if (j.media_id) qc.invalidateQueries({ queryKey: ['mediaDetail', j.media_id] })
      } else if (j.status === 'failed') {
        toast.error(t('shell.jobs.toastFailed', label), { description: j.error_message ?? subject, action: { label: t('common.view'), onClick: () => navigate('/jobs?status=failed') } })
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
      aria-label={t('shell.jobs.queue')}
    >
      <span className="relative grid size-6 shrink-0 place-items-center">
        <Activity className={cn('size-4', running ? 'text-primary' : 'text-fg-3')} />
        {running > 0 && <span className="absolute -right-0.5 -top-0.5 size-2 animate-pulse-dot rounded-full bg-primary text-primary" />}
        {!running && failed > 0 && <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-danger" />}
      </span>
      {!collapsed && (
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2 text-[13px] font-medium text-fg">
            <span className="shrink-0">{t('shell.jobs.title')}</span>
            <span className="truncate text-[11px] font-normal tabular text-fg-3">{t('shell.jobs.summary', running, queued)}</span>
          </span>
          {top ? (
            <span className="mt-1.5 block">
              <Progress value={top.progress} className="h-1" />
              <span className="mt-1 block truncate text-[11px] text-fg-3">
                {jobKind[top.kind]}
                {top.media_title ? `${t('common.colon')}${top.media_title}` : ''}
              </span>
            </span>
          ) : (
            <span className="mt-0.5 block truncate text-[11px] text-fg-3">{failed ? t('shell.jobs.failedPending', failed) : t('shell.jobs.idle')}</span>
          )}
        </span>
      )}
    </button>
  )
  return collapsed ? (
    <Tooltip content={t('shell.jobs.tooltip', running, queued)} side="right">
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
      title={t('shell.jobs.queue')}
      description={t('shell.jobs.drawerDesc')}
      headerActions={
        <Button asChild size="sm" variant="ghost">
          <Link to="/jobs" onClick={() => onOpenChange(false)}>
            {t('shell.jobs.viewAll')}
          </Link>
        </Button>
      }
    >
      <div className="sticky top-0 z-10 border-b border-border bg-surface px-3 py-2">
        <PillTabs
          value={tab}
          onChange={setTab}
          items={[
            { value: 'active', label: t('shell.jobs.tabActive'), count: (counts.running ?? 0) + (counts.queued ?? 0) },
            { value: 'failed', label: t('shell.jobs.tabFailed'), count: (counts.failed ?? 0) + (counts.blocked ?? 0) + (counts.interrupted ?? 0) },
            { value: 'done', label: t('shell.jobs.tabDone') },
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
        <EmptyState compact icon={<Activity />} title={tab === 'failed' ? t('shell.jobs.emptyFailed') : t('shell.jobs.empty')} description={t('shell.jobs.emptyDesc')} />
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
      <Tooltip content={t('shell.theme.title')} side={collapsed ? 'right' : 'top'}>
        <MenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label={t('shell.theme.title')}>
            {themeIcon[pref]}
          </Button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align={collapsed ? 'start' : 'end'}>
        <MenuLabel>{t('shell.theme.label')}</MenuLabel>
        <MenuRadioGroup value={pref} onValueChange={(v) => setPref(v as ThemePref)}>
          <MenuRadioItem value="system" icon={<Monitor />}>{t('shell.theme.system')}</MenuRadioItem>
          <MenuRadioItem value="light" icon={<Sun />}>{t('shell.theme.light')}</MenuRadioItem>
          <MenuRadioItem value="dark" icon={<Moon />}>{t('shell.theme.dark')}</MenuRadioItem>
        </MenuRadioGroup>
      </MenuContent>
    </Menu>
  )
}

function UserMenu({ user, collapsed }: { user: User; collapsed?: boolean }) {
  const { lang, setLang } = useI18n()
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
          aria-label={t('shell.user.menu')}
        >
          <Avatar name={user.display_name || user.username} size={30} />
          {!collapsed && (
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium leading-4 text-fg">{user.display_name || user.username}</span>
              <span className="block truncate text-[11px] leading-4 text-fg-3">{user.role === 'admin' ? t('common.admin') : t('common.annotator')}</span>
            </span>
          )}
        </button>
      </MenuTrigger>
      <MenuContent align={collapsed ? 'start' : 'end'}>
        <MenuLabel>
          {user.display_name} (@{user.username})
        </MenuLabel>
        <MenuItem icon={<UserRound />} onSelect={() => navigate('/settings/account')}>
          {t('shell.user.account')}
        </MenuItem>
        <MenuItem icon={<KeyRound />} onSelect={() => navigate('/settings/account')}>
          {t('shell.user.changePassword')}
        </MenuItem>
        <MenuSeparator />
        <MenuLabel>
          <span className="inline-flex items-center gap-1.5">
            <Languages className="size-3.5" />
            {t('common.language')}
          </span>
        </MenuLabel>
        <MenuRadioGroup value={lang} onValueChange={(v) => setLang(v as Lang)}>
          {LANGS.map((l) => (
            <MenuRadioItem key={l} value={l}>
              <span lang={l === 'en' ? 'en' : 'zh-Hant-TW'}>{msgs().common.languageNames[l]}</span>
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuItem icon={<LogOut />} danger onSelect={logout}>
          {t('shell.user.logout')}
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
    <nav className={cn('flex flex-col', collapsed ? 'items-center gap-1' : 'gap-4')} aria-label={t('shell.nav.main')}>
      {navGroups().map((g, gi) => {
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
        <Link to="/" className="flex min-w-0 items-center gap-2.5" aria-label={t('common.brand.home')}>
          <BrandMark size={34} />
          {!collapsed && (
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold leading-5 tracking-tight">{t('common.brand.name')}</span>
              <span className="block truncate text-[11px] leading-4 text-fg-3">{t('common.brand.tagline')}</span>
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
          <Tooltip content={collapsed ? t('shell.nav.expandSidebar') : t('shell.nav.collapseSidebar')} side="right">
            <Button variant="ghost" size="icon" onClick={onToggle} aria-label={collapsed ? t('shell.nav.expandSidebar') : t('shell.nav.collapseSidebar')}>
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
      <Button variant="ghost" size="icon" onClick={onMenu} aria-label={t('shell.nav.openMenu')}>
        <MenuIcon />
      </Button>
      <Link to="/" className="flex min-w-0 flex-1 items-center gap-2" aria-label={t('common.brand.home')}>
        <BrandMark size={28} />
        <span className="truncate text-[15px] font-semibold tracking-tight">{t('common.brand.name')}</span>
      </Link>
      <Button variant="ghost" size="icon" onClick={onOpenJobs} aria-label={t('shell.jobs.queue')} className="relative">
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
      aria-label={t('shell.nav.phoneTabs')}
    >
      <div className="grid h-16 grid-cols-5">
        {phoneTabs().map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) =>
              cn('flex min-w-0 flex-col items-center justify-center gap-1 px-0.5 text-[11px] font-medium', isActive ? 'text-primary' : 'text-fg-3')
            }
          >
            <tab.icon className="size-[22px]" strokeWidth={1.8} />
            <span className="max-w-full truncate">{tab.label}</span>
          </NavLink>
        ))}
        <button onClick={onMore} className="flex flex-col items-center justify-center gap-1 text-[11px] font-medium text-fg-3">
          <MoreHorizontal className="size-[22px]" strokeWidth={1.8} />
          {t('shell.nav.more')}
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
      <Sheet
        open={drawer}
        onOpenChange={setDrawer}
        side="left"
        title={
          <span className="flex items-center gap-2.5">
            <BrandMark size={30} />
            <span className="min-w-0">
              <span className="block truncate">{t('common.brand.name')}</span>
              <span className="block truncate text-[11px] font-normal leading-4 text-fg-3">{t('common.brand.tagline')}</span>
            </span>
          </span>
        }
      >
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
