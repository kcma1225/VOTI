import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Navigate, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Cookie, FileUp, KeyRound, Pencil, Plus, ShieldCheck, Trash2, UserRound, Users, Workflow, Youtube } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, onErr, qk, useMe, useSettings, useUsers } from '@/api/queries'
import type { PipelineSettings, QualityThresholds, Role, User, YoutubeSettings } from '@/api/types'
import { fmtDateTime, fmtRelative } from '@/lib/format'
import { Page, PageHeader } from '@/components/layout/page'
import { Button } from '@/components/ui/button'
import { Avatar, Badge, Card, CardBody, CardHeader, Skeleton } from '@/components/ui/primitives'
import { Field, Input, NumberInput, Segmented, Switch } from '@/components/ui/form'
import { Dialog } from '@/components/ui/overlay'
import { NavTabs } from '@/components/ui/tabs'
import { DataTable, type Column } from '@/components/ui/data-table'
import { ErrorState } from '@/components/ui/states'
import { cn } from '@/lib/cn'

function SaveBar({ dirty, saving, onSave, onReset }: { dirty: boolean; saving: boolean; onSave: () => void; onReset: () => void }) {
  return (
    <div className="flex flex-col-reverse gap-2 border-t border-border bg-surface-2/40 px-4 py-3 sm:flex-row sm:items-center sm:justify-end sm:px-5">
      {dirty && <span className="text-xs text-fg-3 sm:mr-auto">有尚未儲存的變更</span>}
      <Button onClick={onReset} disabled={!dirty}>
        還原
      </Button>
      <Button variant="primary" onClick={onSave} disabled={!dirty} loading={saving}>
        儲存設定
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
      toast.success(user ? '已更新使用者' : '已建立使用者')
      onOpenChange(false)
    },
    onError: onErr,
  })
  const valid = user ? display.trim() && (!password || password.length >= 8) : username.trim() && display.trim() && password.length >= 8
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={user ? `編輯 ${user.display_name}` : '新增使用者'}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>取消</Button>
          <Button variant="primary" disabled={!valid} loading={m.isPending} onClick={() => m.mutate()}>
            {user ? '儲存' : '建立'}
          </Button>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(e) => (e.preventDefault(), valid && m.mutate())}>
        {!user && (
          <Field label="帳號" htmlFor="u-name" hint="登入用，建立後不能更改。">
            <Input id="u-name" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" spellCheck={false} />
          </Field>
        )}
        <Field label="顯示名稱" htmlFor="u-display">
          <Input id="u-display" value={display} onChange={(e) => setDisplay(e.target.value)} />
        </Field>
        <Field label="角色">
          <Segmented
            value={role}
            onChange={setRole}
            options={[
              { value: 'annotator', label: '標註員' },
              { value: 'admin', label: '管理員' },
            ]}
          />
        </Field>
        <Field label={user ? '重設密碼' : '密碼'} htmlFor="u-pw" hint={user ? '留空代表不變更。至少 8 個字元。' : '至少 8 個字元。'}>
          <Input id="u-pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {user && <Switch checked={active} onCheckedChange={setActive} label="啟用帳號" description="停用後無法登入，但保留審查紀錄。" />}
      </form>
    </Dialog>
  )
}

function UsersTab() {
  const users = useUsers()
  const [open, setOpen] = useState(false)
  const [edit, setEdit] = useState<User | undefined>()
  const cols: Column<User>[] = [
    {
      id: 'name',
      header: '使用者',
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
    { id: 'role', header: '角色', width: 'w-28', cell: (u) => <Badge tone={u.role === 'admin' ? 'primary' : 'neutral'}>{u.role === 'admin' ? '管理員' : '標註員'}</Badge> },
    { id: 'active', header: '狀態', width: 'w-24', cell: (u) => <Badge tone={u.is_active ? 'success' : 'neutral'} dot>{u.is_active ? '啟用' : '停用'}</Badge> },
    { id: 'login', header: '最近登入', width: 'w-36', hideBelow: 'lg', cell: (u) => <span className="text-xs text-fg-2">{u.last_login_at ? fmtRelative(u.last_login_at) : '從未'}</span> },
    { id: 'created', header: '建立', width: 'w-40', hideBelow: 'xl', cell: (u) => <span className="text-xs tabular text-fg-3">{fmtDateTime(u.created_at)}</span> },
    {
      id: 'act',
      header: '',
      width: 'w-14',
      align: 'right',
      cell: (u) => (
        <Button size="icon-sm" variant="ghost" aria-label="編輯" onClick={() => (setEdit(u), setOpen(true))}>
          <Pencil />
        </Button>
      ),
    },
  ]
  return (
    <Card className="overflow-clip">
      <CardHeader
        title="使用者"
        description="管理員可以變更設定與管理帳號；標註員可以下載、審查與建立資料集。"
        actions={
          <Button size="sm" variant="primary" onClick={() => (setEdit(undefined), setOpen(true))}>
            <Plus /> 新增使用者
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
                    @{u.username} · {u.last_login_at ? `${fmtRelative(u.last_login_at)}登入` : '從未登入'}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Badge tone={u.role === 'admin' ? 'primary' : 'neutral'}>{u.role === 'admin' ? '管理員' : '標註員'}</Badge>
                  {!u.is_active && <Badge>停用</Badge>}
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

/* ───────────── YouTube + cookies ───────────── */
function YoutubeTab() {
  const s = useSettings()
  const qc = useQueryClient()
  const [v, setV] = useState<YoutubeSettings | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  useEffect(() => {
    if (s.data) setV(s.data.youtube)
  }, [s.data])
  const save = useMutation({
    mutationFn: () => api.putSetting('youtube', v!),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success('已儲存 YouTube 設定')),
    onError: onErr,
  })
  const upload = useMutation({
    mutationFn: (f: File) => api.uploadCookies(f),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success('已上傳 cookies.txt')),
    onError: (e) => toast.error(errMsg(e)),
  })
  const remove = useMutation({
    mutationFn: api.deleteCookies,
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success('已刪除 cookies')),
    onError: onErr,
  })
  if (s.isError) return <ErrorState error={s.error} />
  if (!v || !s.data) return <Skeleton className="h-96 rounded-xl" />
  const dirty = JSON.stringify(v) !== JSON.stringify(s.data.youtube)
  const pick = (f?: File | null) => {
    if (!f) return
    if (f.size > 1024 * 1024) return toast.error('檔案太大（上限 1 MB）')
    upload.mutate(f)
  }
  const ck = s.data.cookies
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="下載速率" description="放慢下載可以降低被 YouTube 暫時封鎖（HTTP 429）的機率。" icon={<Youtube />} />
        <CardBody className="divide-y divide-border">
          <Row label="頻寬上限" hint="0 代表不限制。">
            <NumberInput value={v.rate_limit_kbps} onChange={(x) => setV({ ...v, rate_limit_kbps: x ?? 0 })} min={0} step={100} suffix="KB/s" />
          </Row>
          <Row label="每部影片之間等待" hint="下載完成到開始下一部之間的間隔。">
            <NumberInput value={v.sleep_between_downloads_sec} onChange={(x) => setV({ ...v, sleep_between_downloads_sec: x ?? 0 })} min={0} suffix="秒" />
          </Row>
          <Row label="每次請求之間等待" hint="列出頻道、取得影片資訊時的請求間隔。">
            <NumberInput value={v.sleep_between_requests_sec ?? 2} onChange={(x) => setV({ ...v, sleep_between_requests_sec: x ?? 0 })} min={0} step={0.5} suffix="秒" />
          </Row>
          <Row label="同時下載數">
            <NumberInput value={v.max_concurrent_downloads} onChange={(x) => setV({ ...v, max_concurrent_downloads: Math.max(1, x ?? 1) })} min={1} max={8} suffix="部" />
          </Row>
          <Row label="使用 cookies" hint="需要登入才能觀看的影片（年齡限制、會員）才需要。">
            <Switch checked={v.cookies_enabled} onCheckedChange={(x) => setV({ ...v, cookies_enabled: x })} disabled={!ck.present} />
          </Row>
        </CardBody>
        <SaveBar dirty={dirty} saving={save.isPending} onSave={() => save.mutate()} onReset={() => setV(s.data.youtube)} />
      </Card>

      <Card>
        <CardHeader title="cookies.txt" description="Netscape 格式。檔案只存在伺服器上，不會顯示內容。建議使用專用的 YouTube 帳號。" icon={<Cookie />} />
        <CardBody className="space-y-4">
          <div className={cn('flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px]', ck.present ? 'bg-success-soft text-success-fg' : 'bg-surface-2 text-fg-2')}>
            <ShieldCheck className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">{ck.present ? `已上傳 · ${fmtDateTime(ck.updated_at)}` : '尚未上傳 cookies'}</span>
            {ck.present && (
              <Button size="sm" variant="ghost" onClick={() => remove.mutate()} loading={remove.isPending}>
                <Trash2 /> 刪除
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
            <div className="text-[13px] text-fg-2">拖放 cookies.txt 到這裡，或</div>
            <Button size="sm" onClick={() => file.current?.click()} loading={upload.isPending}>
              選擇檔案
            </Button>
            <input ref={file} type="file" accept=".txt,text/plain" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

/* ───────────── pipeline + thresholds ───────────── */
function PipelineTab() {
  const s = useSettings()
  const qc = useQueryClient()
  const [p, setP] = useState<PipelineSettings | null>(null)
  const [t, setT] = useState<QualityThresholds | null>(null)
  useEffect(() => {
    if (s.data) {
      setP(s.data.pipeline)
      setT(s.data.quality_thresholds)
    }
  }, [s.data])
  const saveP = useMutation({ mutationFn: () => api.putSetting('pipeline', p!), onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success('已儲存處理流程設定')), onError: onErr })
  const saveT = useMutation({ mutationFn: () => api.putSetting('quality_thresholds', t!), onSuccess: () => (qc.invalidateQueries({ queryKey: qk.settings }), toast.success('已儲存門檻（新處理的音段生效）')), onError: onErr })
  if (s.isError) return <ErrorState error={s.error} />
  if (!p || !t || !s.data) return <Skeleton className="h-96 rounded-xl" />
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="處理流程" description="變更只影響之後處理的影片；已處理的影片可在影片頁「重新處理」。" icon={<Workflow />} />
        <CardBody className="divide-y divide-border">
          <Row label="Vox-Profile 骨幹模型" hint="whisper-large-v3 較準但較慢；wavlm-large 較快。">
            <Segmented
              value={p.voxprofile_backbone}
              onChange={(x) => setP({ ...p, voxprofile_backbone: x })}
              options={[
                { value: 'whisper-large-v3', label: 'Whisper' },
                { value: 'wavlm-large', label: 'WavLM' },
              ]}
            />
          </Row>
          <Row label="語者分段（pyannote）" hint="關閉後每部影片視為單一語者，速度較快。">
            <Switch checked={p.diarization_enabled} onCheckedChange={(x) => setP({ ...p, diarization_enabled: x })} />
          </Row>
          <Row label="音段最短 / 最長">
            <div className="grid grid-cols-2 gap-2">
              <NumberInput aria-label="最短" value={p.segment_min_sec} onChange={(x) => setP({ ...p, segment_min_sec: x ?? 0 })} step={0.5} suffix="秒" />
              <NumberInput aria-label="最長" value={p.segment_max_sec} onChange={(x) => setP({ ...p, segment_max_sec: x ?? 0 })} step={0.5} suffix="秒" />
            </div>
          </Row>
          <Row label="邊界留白" hint="切分時在字幕時間前後保留的長度。">
            <NumberInput value={p.edge_pad_sec} onChange={(x) => setP({ ...p, edge_pad_sec: x ?? 0 })} step={0.05} suffix="秒" />
          </Row>
        </CardBody>
        <SaveBar dirty={JSON.stringify(p) !== JSON.stringify(s.data.pipeline)} saving={saveP.isPending} onSave={() => saveP.mutate()} onReset={() => setP(s.data.pipeline)} />
      </Card>
      <Card>
        <CardHeader title="品質旗標門檻" description="用來為音段加上 low_snr、low_speech、clipped 等旗標；資料集篩選可另外設定更嚴格的門檻。" />
        <CardBody className="divide-y divide-border">
          <Row label="低 SNR">
            <NumberInput value={t.low_snr_db} onChange={(x) => setT({ ...t, low_snr_db: x ?? 0 })} suffix="dB" />
          </Row>
          <Row label="低語音比例">
            <NumberInput value={t.low_speech_ratio} onChange={(x) => setT({ ...t, low_speech_ratio: x ?? 0 })} step={0.05} min={0} max={1} />
          </Row>
          <Row label="削波比例">
            <NumberInput value={t.clipping_ratio} onChange={(x) => setT({ ...t, clipping_ratio: x ?? 0 })} step={0.0005} min={0} />
          </Row>
        </CardBody>
        <SaveBar dirty={JSON.stringify(t) !== JSON.stringify(s.data.quality_thresholds)} saving={saveT.isPending} onSave={() => saveT.mutate()} onReset={() => setT(s.data.quality_thresholds)} />
      </Card>
    </div>
  )
}

/* ───────────── account ───────────── */
function AccountTab({ me }: { me: User }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const m = useMutation({
    mutationFn: () => api.changePassword(cur, next),
    onSuccess: () => {
      toast.success('已變更密碼')
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
        <CardHeader title="我的帳號" icon={<UserRound />} />
        <CardBody className="flex items-center gap-4">
          <Avatar name={me.display_name || me.username} size={52} />
          <div className="min-w-0">
            <div className="truncate text-base font-semibold">{me.display_name}</div>
            <div className="truncate text-[13px] text-fg-3">
              @{me.username} · {me.role === 'admin' ? '管理員' : '標註員'}
            </div>
            <div className="mt-1 text-xs text-fg-3">帳號建立於 {fmtDateTime(me.created_at)}</div>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="變更密碼" icon={<KeyRound />} />
        <form onSubmit={submit}>
          <CardBody className="space-y-4">
            <Field label="目前密碼" htmlFor="pw-cur">
              <Input id="pw-cur" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
            </Field>
            <Field label="新密碼" htmlFor="pw-new" error={short ? '至少需要 8 個字元' : undefined} hint="至少 8 個字元。">
              <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} invalid={short} />
            </Field>
            <Field label="再輸入一次" htmlFor="pw-again" error={mismatch ? '兩次輸入的密碼不一致' : undefined}>
              <Input id="pw-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} invalid={mismatch} />
            </Field>
            <div className="flex justify-end">
              <Button type="submit" variant="primary" loading={m.isPending} disabled={!cur || next.length < 8 || next !== again}>
                變更密碼
              </Button>
            </div>
          </CardBody>
        </form>
      </Card>
    </div>
  )
}

export default function Settings() {
  const { tab } = useParams()
  const me = useMe().data!
  const admin = me.role === 'admin'
  if (!tab) return <Navigate to={admin ? '/settings/users' : '/settings/account'} replace />
  if (!admin && tab !== 'account') return <Navigate to="/settings/account" replace />
  return (
    <Page>
      <PageHeader title="設定" description={admin ? '帳號、YouTube 下載與處理流程。' : '你的帳號設定。'} />
      <NavTabs
        className="mb-5"
        items={[
          ...(admin
            ? [
                { to: '/settings/users', label: '使用者', icon: <Users /> },
                { to: '/settings/youtube', label: 'YouTube', icon: <Youtube /> },
                { to: '/settings/pipeline', label: '處理流程', icon: <Workflow /> },
              ]
            : []),
          { to: '/settings/account', label: '我的帳號', icon: <UserRound /> },
        ]}
      />
      {tab === 'users' && <UsersTab />}
      {tab === 'youtube' && <YoutubeTab />}
      {tab === 'pipeline' && <PipelineTab />}
      {tab === 'account' && <AccountTab me={me} />}
    </Page>
  )
}
