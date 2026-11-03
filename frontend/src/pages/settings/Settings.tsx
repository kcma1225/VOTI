import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Navigate, useParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CircleHelp, Cookie, FileUp, FlaskConical, KeyRound, Languages as LanguagesIcon, Pencil, Plus, RefreshCw, ScanText, ShieldCheck, Trash2, UserRound, Users, Workflow, Youtube, Zap } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, onErr, qk, useMe, useSettings, useUsers } from '@/api/queries'
import type { OcrModel, OcrSettings, OpenccConfig, PipelineSettings, QualityThresholds, Role, TextSettings, User, ValidationSettings, YoutubeApiKey, YoutubeApiState, YoutubeSettings } from '@/api/types'
import type { Tone } from '@/lib/labels'
import { fmtDateTime, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { Field, Input, NumberInput, Segmented, Switch } from '@/components/ui/form'
import { Dialog, Tooltip } from '@/components/ui/overlay'
import { NavTabs } from '@/components/ui/tabs'
import { DataTable, type Column } from '@/components/ui/data-table'
import { ErrorState } from '@/components/ui/states'
import { cn } from '@/lib/cn'
import { t as tr, useT } from '@/i18n'
import { LanguageSwitcher } from '@/components/layout/language-switcher'
import { Languages } from 'lucide-react'

function SaveBar({ dirty, saving, onSave, onReset }: { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }) {
  const t = useT()
  return (
    <div className="flex flex-col-reverse gap-2 border-t border-border bg-surface-2/40 px-4 py-3 sm:flex-row sm:items-center sm:justify-end sm:px-5">
      {dirty && <span className="text-xs text-fg-3 sm:mr-auto">{t('output.settings.unsaved')}</span>}
      <Button onClick={onReset} disabled={!dirty}>
        {t('output.settings.revert')}
      </Button>
      <Button variant="primary" onClick={onSave} disabled={!dirty} loading={saving}>
        {t('output.settings.saveSettings')}
      </Button>
    </div>
  )
}

function Row({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="grid gap-2 py-4 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] md:items-center md:gap-8">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="mt-0.5 text-xs leading-5 text-fg-3">{hint}</div>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

/* ───────────── users ───────────── */
function UserDialog({ open, onOpenChange, user }: { open: boolean; onOpenChange: (o: boolean) => void; user?: User }) {
  const qc = useQueryClient()
  const t = useT()
  const [username, setUsername] = useState('')
  const [display, setDisplay] = useState('')
  const [role, setRole] = useState<Role>('annotator')
  const [active, setActive] = useState(true)
  const [password, setPassword] = useState('')
  useEffect(() => {
    if (open) {
      setUsername(user?.username ?? '')
      setDisplay(user?.display_name ?? '')
      setRole(user?.role ?? 'annotator')
      setActive(user?.is_active ?? true)
      setPassword('')
    }
  }, [open, user])
  const m = useMutation({
    mutationFn: () =>
      user
        ? api.updateUser(user.id, { display_name: display, role, is_active: active, ...(password ? { password } : {}) })
        : api.createUser({ username: username.trim(), display_name: display, password, role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.users })
      toast.success(user ? tr('output.settings.users.updated') : tr('output.settings.users.created'))
      onOpenChange(false)
    },
    onError: onErr,
  })
  const valid = user ? display.trim() && (!password || password.length >= 8) : username.trim() && display.trim() && password.length >= 8
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={user ? t('output.settings.users.editTitle', user.display_name) : t('output.settings.users.addTitle')}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button variant="primary" disabled={!valid} loading={m.isPending} onClick={() => m.mutate()}>
            {user ? t('common.save') : t('output.settings.users.create')}
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(e) => (e.preventDefault(), valid && m.mutate())}>
        {!user && (
          <Field label={t('output.settings.users.username')} htmlFor="u-name" hint={t('output.settings.users.usernameHint')}>
            <Input id="u-name" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" spellCheck={false} />
          </Field>
        )}
        <Field label={t('output.settings.users.displayName')} htmlFor="u-display">
          <Input id="u-display" value={display} onChange={(e) => setDisplay(e.target.value)} />
        </Field>
        <Field label={t('output.settings.users.role')}>
          <Segmented
            value={role}
            onChange={setRole}
            options={[
              { value: 'annotator', label: t('common.annotator') },
              { value: 'admin', label: t('common.admin') },
            ]}
          />
        </Field>
        <Field label={user ? t('output.settings.users.resetPassword') : t('output.settings.users.password')} htmlFor="u-pw" hint={user ? t('output.settings.users.passwordHintEdit') : t('output.settings.users.passwordHint')}>
          <Input id="u-pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {user && <Switch checked={active} onCheckedChange={setActive} label={t('output.settings.users.active')} description={t('output.settings.users.activeDesc')} />}
      </form>
    </Dialog>
  )
}

function UsersTab() {
  const users = useUsers()
  const t = useT()
  const [open, setOpen] = useState(false)
  const [edit, setEdit] = useState<User | undefined>()
  const cols: Column<User>[] = [
    {
      id: 'name',
      header: t('output.settings.users.colUser'),
      cell: (u) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={u.display_name || u.username} size={32} />
          <div className="min-w-0">
            <div className="truncate text-[13px] font-medium">{u.display_name}</div>
            <div className="truncate font-mono text-[11px] text-fg-3">@{u.username}</div>
          </div>
        </div>
      ),
    },
    { id: 'role', header: t('output.settings.users.colRole'), width: 'w-28', cell: (u) => <Badge tone={u.role === 'admin' ? 'primary' : 'neutral'}>{u.role === 'admin' ? t('common.admin') : t('common.annotator')}</Badge> },
    { id: 'active', header: t('output.settings.users.colStatus'), width: 'w-24', cell: (u) => <Badge tone={u.is_active ? 'success' : 'neutral'} dot>{u.is_active ? t('output.settings.users.enabled') : t('output.settings.users.disabled')}</Badge> },
    { id: 'login', header: t('output.settings.users.colLogin'), width: 'w-36', hideBelow: 'lg', cell: (u) => <span className="text-xs text-fg-2">{u.last_login_at ? fmtRelative(u.last_login_at) : t('output.settings.users.never')}</span> },
    { id: 'created', header: t('output.settings.users.colCreated'), width: 'w-40', hideBelow: 'xl', cell: (u) => <span className="text-xs tabular text-fg-3">{fmtDateTime(u.created_at)}</span> },
    {
      id: 'act',
      header: '',
      width: 'w-14',
      align: 'right',
      cell: (u) => (
        <Button size="icon-sm" variant="ghost" aria-label={t('common.edit')} onClick={() => (setEdit(u), setOpen(true))}>
          <Pencil />
        </Button>
      ),
    },
  ]
  return (
    <Card className="overflow-clip">
      <CardHeader
        title={t('output.settings.users.title')}
        description={t('output.settings.users.desc')}
        actions={
          <Button size="sm" variant="primary" onClick={() => (setEdit(undefined), setOpen(true))}>
            <Plus /> {t('output.settings.users.add')}
          </Button>
        }
      />
      <div className="mt-3 border-t border-border">
        {users.isError ? (
          <ErrorState error={users.error} />
        ) : !users.data ? (
          <Skeleton className="m-4 h-32" />
        ) : (
          <DataTable
            rows={users.data}
            columns={cols}
            onRowClick={(u) => (setEdit(u), setOpen(true))}
            card={(u) => (
              <div className="flex items-center gap-3">
                <Avatar name={u.display_name || u.username} size={36} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{u.display_name}</div>
                  <div className="truncate text-xs text-fg-3">
                    @{u.username} · {u.last_login_at ? t('output.settings.users.lastLogin', fmtRelative(u.last_login_at)) : t('output.settings.users.neverLoggedIn')}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Badge tone={u.role === 'admin' ? 'primary' : 'neutral'}>{u.role === 'admin' ? t('common.admin') : t('common.annotator')}</Badge>
                  {!u.is_active && <Badge>{t('output.settings.users.disabled')}</Badge>}
                </div>
              </div>
            )}
          />
        )}
      </div>
      <UserDialog open={open} onOpenChange={setOpen} user={edit} />
    </Card>
  )
}

/* ───────────── YouTube Data API keys ───────────── */
const KEY_TONE: Record<YoutubeApiKey['status'], Tone> = { ok: 'success', untested: 'neutral', quota_exceeded: 'warning', invalid: 'danger' }
const looksLikeKey = (k: string) => /^[A-Za-z0-9_-]{20,100}$/.test(k)

function ApiKeyRow({ k, quota, onRemove, removing }: { k: YoutubeApiKey; quota: number; onRemove: () => void; removing: boolean }) {
  const t = useT()
  const qc = useQueryClient()
  const test = useMutation({
    mutationFn: () => api.testYoutubeApiKey({ id: k.id }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['youtubeApi'] })
      if (r.status === 'ok') toast.success(tr('ytapi.settings.testOk'))
      else if (r.status === 'quota_exceeded') toast.warning(tr('ytapi.settings.testQuota'))
      else if (r.status === 'invalid') toast.error(tr('ytapi.settings.testInvalid', r.message))
      else toast.error(tr('ytapi.settings.testUnavailable', r.message))
    },
    onError: onErr,
  })
  const statusLabel = t(`ytapi.settings.status.${k.status}`)
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:gap-4" data-key-status={k.status}>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[13px] text-fg">{k.masked}</code>
          {k.label && <span className="min-w-0 truncate text-[13px] text-fg-2">{k.label}</span>}
          {k.source === 'env' && (
            <Tooltip content={t('ytapi.settings.envTip')}>
              <span>
                <Badge tone="info">{t('ytapi.settings.envBadge')}</Badge>
              </span>
            </Tooltip>
          )}
          <Tooltip content={k.status === 'invalid' || k.status === 'quota_exceeded' ? k.last_error : null}>
            <span className="min-w-0">
              <Badge tone={KEY_TONE[k.status]} dot>
                {k.status === 'quota_exceeded' && k.until ? `${statusLabel} · ${t('ytapi.settings.resetAt', fmtDateTime(k.until))}` : statusLabel}
              </Badge>
            </span>
          </Tooltip>
        </div>
        <div className="mt-1 text-xs text-fg-3 tabular">
          {t('ytapi.settings.usage', k.units_today, quota)} · {k.last_used_at ? t('ytapi.settings.lastUsed', fmtRelative(k.last_used_at)) : t('ytapi.settings.neverUsed')}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button size="sm" onClick={() => test.mutate()} loading={test.isPending}>
          <FlaskConical /> {t('ytapi.settings.test')}
        </Button>
        {k.source !== 'env' && (
          <Button size="sm" variant="ghost" onClick={onRemove} loading={removing} aria-label={`${t('ytapi.settings.remove')} ${k.masked}`}>
            <Trash2 /> {t('ytapi.settings.remove')}
          </Button>
        )}
      </div>
    </li>
  )
}

function YoutubeApiCard({ initial }: { initial?: YoutubeApiState }) {
  const t = useT()
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['youtubeApi'],
    queryFn: api.youtubeApi,
    initialData: initial,
    refetchInterval: (x) => (x.state.data?.backfill.active ? 5000 : false),
  })
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [removeK, setRemoveK] = useState<YoutubeApiKey | null>(null)
  const st = q.data
  const stored = (st?.keys ?? []).filter((k) => k.source !== 'env')
  const after = (r: YoutubeApiState) => {
    qc.setQueryData(['youtubeApi'], r)
    qc.invalidateQueries({ queryKey: qk.settings })
    if (r.backfill_queued) toast.success(tr('ytapi.settings.autoStarted'), { description: tr('ytapi.settings.backfillPending', r.backfill.pending) })
  }
  const add = useMutation({
    mutationFn: () => api.putYoutubeApiKeys([...stored.map((k) => ({ id: k.id })), { key: key.trim(), label: label.trim() || undefined }]),
    onSuccess: (r) => {
      after(r)
      setKey('')
      setLabel('')
      if (!r.backfill_queued) toast.success(tr('ytapi.settings.added'))
    },
    onError: onErr,
  })
  const remove = useMutation({
    mutationFn: (id: string) => api.putYoutubeApiKeys(stored.filter((k) => k.id !== id).map((k) => ({ id: k.id }))),
    onSuccess: (r) => (after(r), setRemoveK(null), toast.success(tr('ytapi.settings.removed'))),
    onError: onErr,
  })
  const backfill = useMutation({
    mutationFn: api.youtubeApiBackfill,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['youtubeApi'] })
      qc.invalidateQueries({ queryKey: ['channels'] })
      if (r.already_active) toast(tr('ytapi.settings.backfillAlready'), { description: tr('ytapi.settings.backfillActive', r.queued) })
      else toast.success(tr('ytapi.settings.backfillQueued', r.queued))
    },
    onError: onErr,
  })
  if (q.isError) return <ErrorState error={q.error} />
  if (!st) return <Skeleton className="h-64 rounded-xl" />
  const trimmed = key.trim()
  const badFormat = trimmed.length > 0 && !looksLikeKey(trimmed)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!trimmed || badFormat) return
    add.mutate()
  }
  const bf = st.backfill
  return (
    <Card data-shot="ytapi-card">
      <CardHeader title={t('ytapi.settings.title')} description={t('ytapi.settings.desc')} icon={<KeyRound />} />
      <CardBody className="space-y-4">
        {st.keys.length ? (
          <ul className="divide-y divide-border rounded-lg border border-border px-3.5 py-3">
            {st.keys.map((k) => (
              <ApiKeyRow key={k.id} k={k} quota={st.daily_quota} onRemove={() => setRemoveK(k)} removing={remove.isPending && remove.variables === k.id} />
            ))}
          </ul>
        ) : (
          <div className="rounded-lg bg-surface-2 px-3 py-2.5 text-[13px] text-fg-2">{t('ytapi.settings.empty')}</div>
        )}
        <form onSubmit={submit} className="space-y-2" aria-label={t('ytapi.settings.addLabel')}>
          <div className="text-sm font-medium">{t('ytapi.settings.addLabel')}</div>
          <div className="grid gap-2 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]">
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              autoCapitalize="none"
              placeholder={t('ytapi.settings.keyPlaceholder')}
              aria-label={t('ytapi.settings.keyPlaceholder')}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              invalid={badFormat}
              className="font-mono"
            />
            <Input placeholder={t('ytapi.settings.labelPlaceholder')} aria-label={t('ytapi.settings.labelPlaceholder')} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} />
            <Button type="submit" variant="primary" disabled={!trimmed || badFormat} loading={add.isPending}>
              <Plus /> {t('ytapi.settings.add')}
            </Button>
          </div>
          {badFormat && <p className="text-xs text-danger-fg">{t('ytapi.settings.invalidFormat')}</p>}
          <p className="text-xs leading-5 text-fg-3">{t('ytapi.settings.rotationHint', Math.max(1, st.keys.length), st.daily_quota)}</p>
        </form>
        <details className="group rounded-lg border border-border px-3.5 py-2.5 text-[13px]">
          <summary className="flex cursor-pointer list-none items-center gap-2 font-medium text-fg-2 [&::-webkit-details-marker]:hidden">
            <CircleHelp className="size-4 shrink-0 text-fg-3" /> {t('ytapi.settings.howTitle')}
          </summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs leading-5 text-fg-2">
            <li>{t('ytapi.settings.how.s1')}</li>
            <li>{t('ytapi.settings.how.s2')}</li>
            <li>{t('ytapi.settings.how.s3')}</li>
            <li>{t('ytapi.settings.how.s4')}</li>
            <li>{t('ytapi.settings.how.s5')}</li>
          </ol>
        </details>
      </CardBody>
      <div className="border-t border-border px-4 py-4 sm:px-5">
        <Row
          label={t('ytapi.settings.backfill')}
          hint={
            <>
              {t('ytapi.settings.backfillHint')}
              {bf.langs_pending > 0 && <span className="mt-0.5 block">{t('ytapi.settings.backfillLangs', bf.langs_pending)}</span>}
            </>
          }
        >
          <div className="flex flex-col items-stretch gap-1.5 md:items-end">
            <Button onClick={() => backfill.mutate()} loading={backfill.isPending} disabled={!st.usable || bf.active || bf.pending === 0}>
              <Zap /> {t('ytapi.settings.backfill')}
            </Button>
            <span className={cn('text-xs tabular md:text-right', bf.active ? 'text-primary' : 'text-fg-3')} role="status">
              {!st.usable ? t('ytapi.settings.backfillNoKey') : bf.active ? t('ytapi.settings.backfillActive', bf.pending) : bf.pending ? t('ytapi.settings.backfillPending', bf.pending) : t('ytapi.settings.backfillNone')}
            </span>
          </div>
        </Row>
      </div>
      <Dialog
        open={!!removeK}
        onOpenChange={(o) => !o && setRemoveK(null)}
        title={t('ytapi.settings.removeTitle')}
        description={removeK ? t('ytapi.settings.removeDesc', removeK.masked) : undefined}
        size="sm"
        footer={
          <>
            <Button onClick={() => setRemoveK(null)}>{t('common.cancel')}</Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => removeK && remove.mutate(removeK.id)}>
              <Trash2 /> {t('ytapi.settings.remove')}
            </Button>
          </>
        }
      />
    </Card>
  )
}

/* ───────────── YouTube + cookies ───────────── */
function YoutubeTab() {
  const s = useSettings()
  const qc = useQueryClient()
  const t = useT()
  const [v, setV] = useState<YoutubeSettings | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  useEffect(() => {
    if (s.data) setV(s.data.youtube)
  }, [s.data])
  const save = useMutation({
    mutationFn: () => api.putSetting('youtube', v!),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.youtube.saved'))),
    onError: onErr,
  })
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadCookies(f),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.youtube.uploaded'))),
    onError: (e) => toast.error(errMsg(e)),
  })
  const remove = useMutation({
    mutationFn: api.deleteCookies,
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.youtube.deleted'))),
    onError: onErr,
  })
  if (s.isError) return <ErrorState error={s.error} />
  if (!v || !s.data) return <Skeleton className="h-96 rounded-xl" />
  const dirty = JSON.stringify(v) !== JSON.stringify(s.data.youtube)
  const pick = (f?: File | null) => {
    if (!f) return
    if (f.size > 1024 * 1024) return toast.error(tr('output.settings.youtube.tooLarge'))
    upload.mutate(f)
  }
  const ck = s.data.cookies
  return (
    <div className="space-y-4">
      <YoutubeApiCard initial={s.data.youtube_api} />
      <Card>
        <CardHeader title={t('output.settings.youtube.rateTitle')} description={t('output.settings.youtube.rateDesc')} icon={<Youtube />} />
        <CardBody className="divide-y divide-border">
          <Row label={t('output.settings.youtube.bandwidth')} hint={t('output.settings.youtube.bandwidthHint')}>
            <NumberInput value={v.rate_limit_kbps} onChange={(x) => setV({ ...v, rate_limit_kbps: x ?? 0 })} min={0} step={100} suffix="KB/s" />
          </Row>
          <Row label={t('output.settings.youtube.sleepDownloads')} hint={t('output.settings.youtube.sleepDownloadsHint')}>
            <NumberInput value={v.sleep_between_downloads_sec} onChange={(x) => setV({ ...v, sleep_between_downloads_sec: x ?? 0 })} min={0} suffix={t('output.settings.youtube.sec')} />
          </Row>
          <Row label={t('output.settings.youtube.sleepRequests')} hint={t('output.settings.youtube.sleepRequestsHint')}>
            <NumberInput value={v.sleep_between_requests_sec ?? 2} onChange={(x) => setV({ ...v, sleep_between_requests_sec: x ?? 0 })} min={0} step={0.25} suffix={t('output.settings.youtube.sec')} />
          </Row>
          <Row label={t('output.settings.youtube.concurrent')}>
            <NumberInput value={v.max_concurrent_downloads} onChange={(x) => setV({ ...v, max_concurrent_downloads: Math.max(1, x ?? 1) })} min={1} max={8} suffix={t('output.settings.youtube.videosUnit')} />
          </Row>
          <Row label={t('output.settings.youtube.useCookies')} hint={t('output.settings.youtube.useCookiesHint')}>
            <Switch checked={v.cookies_enabled} onCheckedChange={(x) => setV({ ...v, cookies_enabled: x })} disabled={!ck.present} />
          </Row>
          <Row label={t('output.settings.youtube.verifyCaptions')} hint={t('output.settings.youtube.verifyCaptionsHint')}>
            <Switch checked={v.verify_captions ?? true} onCheckedChange={(x) => setV({ ...v, verify_captions: x })} aria-label={t('output.settings.youtube.verifyCaptions')} />
          </Row>
        </CardBody>
        <SaveBar dirty={dirty} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setV(s.data.youtube)} />
      </Card>

      <AutoRefreshCard current={s.data.youtube.auto_refresh_hours ?? 0} />

      <Card>
        <CardHeader title="cookies.txt" description={t('output.settings.youtube.cookiesDesc')} icon={<Cookie />} />
        <CardBody className="space-y-4">
          <div className={cn('flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px]', ck.present ? 'bg-success-soft text-success-fg' : 'bg-surface-2 text-fg-2')}>
            <ShieldCheck className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{ck.present ? t('output.settings.youtube.uploadedAt', fmtDateTime(ck.updated_at)) : t('output.settings.youtube.notUploaded')}</span>
            {ck.present && (
              <Button size="sm" variant="ghost" onClick={() => remove.mutate()} loading={remove.isPending}>
                <Trash2 /> {t('common.delete')}
              </Button>
            )}
          </div>
          <div
            onDragOver={(e) => (e.preventDefault(), setDrag(true))}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDrag(false)
              pick(e.dataTransfer.files[0])
            }}
            className={cn('flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors', drag ? 'border-primary bg-primary-soft' : 'border-border-strong')}
          >
            <FileUp className="size-6 text-fg-3" />
            <div className="text-[13px] text-fg-2">{t('output.settings.youtube.dropHere')}</div>
            <Button size="sm" onClick={() => file.current?.click()} loading={upload.isPending}>
              {t('output.settings.youtube.chooseFile')}
            </Button>
            <input ref={file} type="file" accept=".txt,text/plain" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

function AutoRefreshCard({ current }: { current: number }) {
  const t = useT()
  const qc = useQueryClient()
  const [h, setH] = useState<number>(current)
  useEffect(() => setH(current), [current])
  const save = useMutation({
    mutationFn: () => api.putSetting('youtube', { auto_refresh_hours: h } as YoutubeSettings),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.youtube.saved'))),
    onError: onErr,
  })
  return (
    <Card>
      <CardHeader title={t('output.settings.youtube.autoRefreshTitle')} description={t('output.settings.youtube.autoRefreshDesc')} icon={<RefreshCw />} />
      <CardBody>
        <Row label={t('output.settings.youtube.autoRefresh')} hint={t('output.settings.youtube.autoRefreshHint')}>
          <NumberInput aria-label={t('output.settings.youtube.autoRefresh')} value={h} onChange={(x) => setH(Math.max(0, Math.round(x ?? 0)))} min={0} step={1} suffix={h ? t('output.settings.youtube.hours') : t('output.settings.youtube.autoRefreshOff')} />
        </Row>
      </CardBody>
      <SaveBar dirty={h !== current} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setH(current)} />
    </Card>
  )
}

/* ───────────── caption text (Simplified → Traditional) ───────────── */
const TEXT_DEFAULT: TextSettings = { convert_simplified: true, opencc_config: 's2twp' }
function TextCard({ current }: { current: TextSettings }) {
  const t = useT()
  const qc = useQueryClient()
  const [v, setV] = useState<TextSettings>(current)
  useEffect(() => setV(current), [current])
  const save = useMutation({
    mutationFn: () => api.putSetting('text', v),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.text.saved'))),
    onError: onErr,
  })
  const opts: [OpenccConfig, string, string][] = [
    ['s2twp', t('output.settings.text.s2twp'), t('output.settings.text.s2twpHint')],
    ['s2tw', t('output.settings.text.s2tw'), t('output.settings.text.s2twHint')],
    ['s2t', t('output.settings.text.s2t'), t('output.settings.text.s2tHint')],
  ]
  return (
    <Card>
      <CardHeader title={t('output.settings.text.title')} description={t('output.settings.text.desc')} icon={<LanguagesIcon />} />
      <CardBody className="divide-y divide-border">
        <Row label={t('output.settings.text.convert')} hint={t('output.settings.text.convertHint')}>
          <Switch checked={v.convert_simplified} onCheckedChange={(x) => setV({ ...v, convert_simplified: x })} aria-label={t('output.settings.text.convert')} />
        </Row>
        <Row label={t('output.settings.text.config')} hint={t('output.settings.text.configHint')}>
          <div role="radiogroup" aria-label={t('output.settings.text.config')} className={cn('space-y-1.5', !v.convert_simplified && 'pointer-events-none opacity-50')}>
            {opts.map(([k, label, hint]) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={v.opencc_config === k}
                disabled={!v.convert_simplified}
                onClick={() => setV({ ...v, opencc_config: k })}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors',
                  v.opencc_config === k ? 'border-primary/50 bg-primary-soft/60' : 'border-border hover:bg-surface-2',
                )}
              >
                <span className={cn('mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border', v.opencc_config === k ? 'border-primary' : 'border-border-strong')}>
                  {v.opencc_config === k && <span className="size-2 rounded-full bg-primary" />}
                </span>
                <span className="min-w-0">
                  <span className="flex flex-wrap items-baseline gap-x-2 text-[13px] font-medium">
                    {label} <code className="font-mono text-[11px] font-normal text-fg-3">{k}</code>
                  </span>
                  <span className="block text-xs leading-5 text-fg-3">{hint}</span>
                </span>
              </button>
            ))}
          </div>
        </Row>
      </CardBody>
      <SaveBar dirty={JSON.stringify(v) !== JSON.stringify(current)} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setV(current)} />
    </Card>
  )
}

/* ───────────── caption ↔ audio validation ───────────── */
const VALIDATION_DEFAULT: ValidationSettings = { enabled: true, lid_min_prob: 0.7, lid_min_speech_sec: 1, align_min_score: 0.4, align_min_units: 4 }
function ValidationCard({ current }: { current: ValidationSettings }) {
  const t = useT()
  const qc = useQueryClient()
  const [v, setV] = useState<ValidationSettings>(current)
  useEffect(() => setV(current), [current])
  const save = useMutation({
    mutationFn: () => api.putSetting('validation', v),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('validate.settings.saved'))),
    onError: onErr,
  })
  const backfill = useMutation({
    mutationFn: () => api.validateBackfill(),
    onSuccess: (r) => (r.queued ? toast.success(tr('validate.settings.backfillQueued', r.queued)) : toast(tr('validate.settings.backfillQueued', 0))),
    onError: onErr,
  })
  const off = !v.enabled && 'pointer-events-none opacity-50'
  return (
    <Card data-shot="validation-card">
      <CardHeader title={t('validate.settings.title')} description={t('validate.settings.desc')} icon={<ShieldCheck />} />
      <CardBody className="divide-y divide-border">
        <Row label={t('validate.settings.enabled')} hint={t('validate.settings.enabledHint')}>
          <Switch checked={v.enabled} onCheckedChange={(x) => setV({ ...v, enabled: x })} aria-label={t('validate.settings.enabled')} />
        </Row>
        <Row label={t('validate.settings.lidMinProb')} hint={t('validate.settings.lidMinProbHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.lid_min_prob} onChange={(x) => setV({ ...v, lid_min_prob: x ?? 0 })} step={0.05} min={0} max={1} />
          </div>
        </Row>
        <Row label={t('validate.settings.lidMinSpeech')} hint={t('validate.settings.lidMinSpeechHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.lid_min_speech_sec} onChange={(x) => setV({ ...v, lid_min_speech_sec: x ?? 0 })} step={0.5} min={0} max={10} suffix={t('output.settings.youtube.sec')} />
          </div>
        </Row>
        <Row label={t('validate.settings.alignMinScore')} hint={t('validate.settings.alignMinScoreHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.align_min_score} onChange={(x) => setV({ ...v, align_min_score: x ?? 0 })} step={0.05} min={0} max={1} />
          </div>
        </Row>
        <Row label={t('validate.settings.alignMinUnits')} hint={t('validate.settings.alignMinUnitsHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.align_min_units} onChange={(x) => setV({ ...v, align_min_units: Math.max(1, Math.round(x ?? 1)) })} step={1} min={1} max={100} />
          </div>
        </Row>
        <Row label={t('validate.settings.backfill')} hint={t('validate.settings.backfillHint')}>
          <Button onClick={() => backfill.mutate()} loading={backfill.isPending} disabled={!current.enabled}>
            <ShieldCheck /> {t('validate.settings.backfill')}
          </Button>
        </Row>
      </CardBody>
      <SaveBar dirty={JSON.stringify(v) !== JSON.stringify(current)} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setV(current)} />
    </Card>
  )
}

/* ───────────── hard-subtitle OCR ───────────── */
const OCR_DEFAULT: OcrSettings = {
  enabled: true, auto_when_no_captions: true, fps: 5, discovery_step_sec: 2, max_discovery_frames: 900,
  model: 'server', cpu_model: 'mobile', keep_crops: true, min_event_conf: 0.85,
}
const clampN = (x: number, lo: number, hi: number) => (Number.isNaN(x) ? lo : Math.min(hi, Math.max(lo, x)))
/** the API's ranges (OcrSettings in backend/voti/api/schemas.py); values are typed freely and clamped on save */
const normOcr = (v: OcrSettings): OcrSettings => ({
  ...v,
  fps: clampN(v.fps, 1, 15),
  discovery_step_sec: clampN(v.discovery_step_sec, 0.5, 10),
  max_discovery_frames: Math.round(clampN(v.max_discovery_frames, 50, 5000)),
  min_event_conf: clampN(v.min_event_conf, 0, 1),
})
function OcrSettingsCard({ current }: { current: OcrSettings }) {
  const t = useT()
  const qc = useQueryClient()
  const [v, setV] = useState<OcrSettings>(current)
  useEffect(() => setV(current), [current])
  const save = useMutation({
    mutationFn: () => api.putSetting('ocr', normOcr(v)),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('ocr.settings.saved'))),
    onError: onErr,
  })
  const off = !v.enabled && 'pointer-events-none opacity-50'
  const models = [
    { value: 'server' as OcrModel, label: t('ocr.settings.server') },
    { value: 'mobile' as OcrModel, label: t('ocr.settings.mobile') },
  ]
  return (
    <Card data-shot="ocr-settings-card">
      <CardHeader title={t('ocr.settings.title')} description={t('ocr.settings.desc')} icon={<ScanText />} />
      <CardBody className="divide-y divide-border">
        <Row label={t('ocr.settings.enabled')} hint={t('ocr.settings.enabledHint')}>
          <Switch checked={v.enabled} onCheckedChange={(x) => setV({ ...v, enabled: x })} aria-label={t('ocr.settings.enabled')} />
        </Row>
        <Row label={t('ocr.settings.auto')} hint={t('ocr.settings.autoHint')}>
          <div className={cn(off)}>
            <Switch checked={v.auto_when_no_captions} onCheckedChange={(x) => setV({ ...v, auto_when_no_captions: x })} aria-label={t('ocr.settings.auto')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.fps')} hint={t('ocr.settings.fpsHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.fps} onChange={(x) => setV({ ...v, fps: x ?? 0 })} step={1} min={1} max={15} suffix="fps" />
          </div>
        </Row>
        <Row label={t('ocr.settings.discovery')} hint={t('ocr.settings.discoveryHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.discovery_step_sec} onChange={(x) => setV({ ...v, discovery_step_sec: x ?? 0 })} step={0.5} min={0.5} max={10} suffix={t('ocr.settings.sec')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.maxDiscovery')} hint={t('ocr.settings.maxDiscoveryHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.max_discovery_frames} onChange={(x) => setV({ ...v, max_discovery_frames: x ?? 0 })} step={50} min={50} max={5000} suffix={t('ocr.settings.frames')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.model')} hint={t('ocr.settings.modelHint')}>
          <div className={cn(off)}>
            <Segmented value={v.model} onChange={(x) => setV({ ...v, model: x })} options={models} aria-label={t('ocr.settings.model')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.cpuModel')} hint={t('ocr.settings.cpuModelHint')}>
          <div className={cn(off)}>
            <Segmented value={v.cpu_model} onChange={(x) => setV({ ...v, cpu_model: x })} options={models} aria-label={t('ocr.settings.cpuModel')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.keepCrops')} hint={t('ocr.settings.keepCropsHint')}>
          <div className={cn(off)}>
            <Switch checked={v.keep_crops} onCheckedChange={(x) => setV({ ...v, keep_crops: x })} aria-label={t('ocr.settings.keepCrops')} />
          </div>
        </Row>
        <Row label={t('ocr.settings.minConf')} hint={t('ocr.settings.minConfHint')}>
          <div className={cn(off)}>
            <NumberInput value={v.min_event_conf} onChange={(x) => setV({ ...v, min_event_conf: x ?? 0 })} step={0.01} min={0} max={1} />
          </div>
        </Row>
      </CardBody>
      <SaveBar dirty={JSON.stringify(v) !== JSON.stringify(current)} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setV(current)} />
    </Card>
  )
}

/* ───────────── pipeline + thresholds ───────────── */
function PipelineTab() {
  const s = useSettings()
  const qc = useQueryClient()
  const t = useT()
  const [p, setP] = useState<PipelineSettings | null>(null)
  const [th, setTh] = useState<QualityThresholds | null>(null)
  useEffect(() => {
    if (s.data) {
      setP(s.data.pipeline)
      setTh(s.data.quality_thresholds)
    }
  }, [s.data])
  const saveP = useMutation({ mutationFn: () => api.putSetting('pipeline', p!), onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.pipeline.saved'))), onError: onErr })
  const saveT = useMutation({ mutationFn: () => api.putSetting('quality_thresholds', th!), onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success(tr('output.settings.pipeline.thresholdsSaved'))), onError: onErr })
  if (s.isError) return <ErrorState error={s.error} />
  if (!p || !th || !s.data) return <Skeleton className="h-96 rounded-xl" />
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title={t('output.settings.pipeline.title')} description={t('output.settings.pipeline.desc')} icon={<Workflow />} />
        <CardBody className="divide-y divide-border">
          <Row label={t('output.settings.pipeline.backbone')} hint={t('output.settings.pipeline.backboneHint')}>
            <Segmented
              value={p.voxprofile_backbone}
              onChange={(x) => setP({ ...p, voxprofile_backbone: x })}
              options={[
                { value: 'whisper-large-v3', label: 'Whisper' },
                { value: 'wavlm-large', label: 'WavLM' },
              ]}
            />
          </Row>
          <Row label={t('output.settings.pipeline.diarization')} hint={t('output.settings.pipeline.diarizationHint')}>
            <Switch checked={p.diarization_enabled} onCheckedChange={(x) => setP({ ...p, diarization_enabled: x })} />
          </Row>
          <Row label={t('output.settings.pipeline.segLength')}>
            <div className="grid grid-cols-2 gap-2">
              <NumberInput aria-label={t('output.settings.pipeline.min')} value={p.segment_min_sec} onChange={(x) => setP({ ...p, segment_min_sec: x ?? 0 })} step={0.5} suffix={t('output.settings.youtube.sec')} />
              <NumberInput aria-label={t('output.settings.pipeline.max')} value={p.segment_max_sec} onChange={(x) => setP({ ...p, segment_max_sec: x ?? 0 })} step={0.5} suffix={t('output.settings.youtube.sec')} />
            </div>
          </Row>
          <Row label={t('output.settings.pipeline.edgePad')} hint={t('output.settings.pipeline.edgePadHint')}>
            <NumberInput value={p.edge_pad_sec} onChange={(x) => setP({ ...p, edge_pad_sec: x ?? 0 })} step={0.05} suffix={t('output.settings.youtube.sec')} />
          </Row>
        </CardBody>
        <SaveBar dirty={JSON.stringify(p) !== JSON.stringify(s.data.pipeline)} saving={saveP.isPending} onSave={() => saveP.mutate()} onReset={() => setP(s.data.pipeline)} />
      </Card>
      <Card>
        <CardHeader title={t('output.settings.pipeline.thresholdsTitle')} description={t('output.settings.pipeline.thresholdsDesc')} />
        <CardBody className="divide-y divide-border">
          <Row label={t('output.settings.pipeline.lowSnr')}>
            <NumberInput value={th.low_snr_db} onChange={(x) => setTh({ ...th, low_snr_db: x ?? 0 })} suffix="dB" />
          </Row>
          <Row label={t('output.settings.pipeline.lowSpeech')}>
            <NumberInput value={th.low_speech_ratio} onChange={(x) => setTh({ ...th, low_speech_ratio: x ?? 0 })} step={0.05} min={0} max={1} />
          </Row>
          <Row label={t('output.settings.pipeline.clipping')}>
            <NumberInput value={th.clipping_ratio} onChange={(x) => setTh({ ...th, clipping_ratio: x ?? 0 })} step={0.0005} min={0} />
          </Row>
        </CardBody>
        <SaveBar dirty={JSON.stringify(th) !== JSON.stringify(s.data.quality_thresholds)} saving={saveT.isPending} onSave={() => saveT.mutate()} onReset={() => setTh(s.data.quality_thresholds)} />
      </Card>
      <ValidationCard current={s.data.validation ?? VALIDATION_DEFAULT} />
      <OcrSettingsCard current={s.data.ocr ?? OCR_DEFAULT} />
      <TextCard current={s.data.text ?? TEXT_DEFAULT} />
    </div>
  )
}

/* ───────────── account ───────────── */
function AccountTab({ me }: { me: User }) {
  const t = useT()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const m = useMutation({
    mutationFn: () => api.changePassword(cur, next),
    onSuccess: () => {
      toast.success(tr('output.settings.account.passwordChanged'))
      setCur('')
      setNext('')
      setAgain('')
    },
    onError: onErr,
  })
  const mismatch = again.length > 0 && next !== again
  const short = next.length > 0 && next.length < 8
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (cur && next.length >= 8 && next === again) m.mutate()
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader title={t('output.settings.account.title')} icon={<UserRound />} />
        <CardBody className="flex items-center gap-4">
          <Avatar name={me.display_name || me.username} size={52} />
          <div className="min-w-0">
            <div className="truncate text-base font-semibold">{me.display_name}</div>
            <div className="truncate text-[13px] text-fg-3">
              @{me.username} · {me.role === 'admin' ? t('common.admin') : t('common.annotator')}
            </div>
            <div className="mt-1 text-xs text-fg-3">{t('output.settings.account.createdAt', fmtDateTime(me.created_at))}</div>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title={t('output.settings.account.changePassword')} icon={<KeyRound />} />
        <form onSubmit={submit}>
          <CardBody className="space-y-4">
            <Field label={t('output.settings.account.current')} htmlFor="pw-cur">
              <Input id="pw-cur" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
            </Field>
            <Field label={t('output.settings.account.new')} htmlFor="pw-new" error={short ? t('output.settings.account.tooShort') : undefined} hint={t('output.settings.account.newHint')}>
              <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} invalid={short} />
            </Field>
            <Field label={t('output.settings.account.again')} htmlFor="pw-again" error={mismatch ? t('output.settings.account.mismatch') : undefined}>
              <Input id="pw-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} invalid={mismatch} />
            </Field>
            <div className="flex justify-end">
              <Button type="submit" variant="primary" loading={m.isPending} disabled={!cur || next.length < 8 || next !== again}>
                {t('output.settings.account.changePassword')}
              </Button>
            </div>
          </CardBody>
        </form>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader title={t('common.language')} description={t('output.settings.account.languageDesc')} icon={<Languages />} />
        <CardBody>
          <LanguageSwitcher size="md" />
        </CardBody>
      </Card>
    </div>
  )
}

export default function Settings() {
  const { tab } = useParams()
  const me = useMe().data!
  const admin = me.role === 'admin'
  const t = useT()
  if (!tab) return <Navigate to={admin ? '/settings/users' : '/settings/account'} replace />
  if (!admin && tab !== 'account') return <Navigate to="/settings/account" replace />
  return (
    <Page>
      <PageHeader title={t('output.settings.title')} description={admin ? t('output.settings.descAdmin') : t('output.settings.descUser')} />
      <NavTabs
        className="mb-5"
        items={[
          ...(admin
            ? [
                { to: '/settings/users', label: t('output.settings.tabs.users'), icon: <Users /> },
                { to: '/settings/youtube', label: t('output.settings.tabs.youtube'), icon: <Youtube /> },
                { to: '/settings/pipeline', label: t('output.settings.tabs.pipeline'), icon: <Workflow /> },
              ]
            : []),
          { to: '/settings/account', label: t('output.settings.tabs.account'), icon: <UserRound /> },
        ]}
      />
      {tab === 'users' && <UsersTab />}
      {tab === 'youtube' && <YoutubeTab />}
      {tab === 'pipeline' && <PipelineTab />}
      {tab === 'account' && <AccountTab me={me} />}
    </Page>
  )
}
