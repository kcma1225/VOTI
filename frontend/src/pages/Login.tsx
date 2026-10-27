import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Eye, EyeOff } from 'lucide-react'
import { api } from '@/api/client'
import { errMsg, qk } from '@/api/queries'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/form'
import { useTitle } from '@/components/layout/page'

function Bars() {
  // decorative waveform built from deterministic heights
  const hs = Array.from({ length: 64 }, (_, i) => 0.25 + 0.75 * Math.abs(Math.sin(i * 0.37) * Math.cos(i * 0.11 + 1.3)))
  return (
    <div className="flex h-28 items-center gap-[3px]" aria-hidden>
      {hs.map((h, i) => (
        <span key={i} className="w-[3px] rounded-full bg-white/70" style={{ height: `${h * 100}%`, opacity: 0.35 + h * 0.65 }} />
      ))}
    </div>
  )
}

export default function Login() {
  useTitle('登入')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [sp] = useSearchParams()

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (!username || !password) {
      setError('請輸入帳號與密碼')
      return
    }
    setBusy(true)
    try {
      const u = await api.login(username.trim(), password)
      qc.setQueryData(qk.me, u)
      const next = sp.get('next')
      navigate(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replace: true })
    } catch (err) {
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <aside className="relative hidden overflow-hidden bg-[#2b44b8] text-white lg:flex lg:flex-col lg:justify-between lg:p-12 dark:bg-[#1c2c78]">
        <div
          className="pointer-events-none absolute inset-0 opacity-90"
          style={{
            background:
              'radial-gradient(1200px 500px at 0% 0%, rgba(255,255,255,0.18), transparent 60%), radial-gradient(800px 400px at 100% 100%, rgba(27,175,122,0.35), transparent 60%)',
          }}
        />
        <div className="relative flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-white/15 backdrop-blur">
            <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" aria-hidden>
              <rect x="3" y="9.5" width="2.6" height="5" rx="1.3" />
              <rect x="7.6" y="6.5" width="2.6" height="11" rx="1.3" />
              <rect x="12.2" y="3.5" width="2.6" height="17" rx="1.3" />
              <rect x="16.8" y="8" width="2.6" height="8" rx="1.3" />
            </svg>
          </div>
          <span className="text-lg font-semibold tracking-tight">VOTI</span>
        </div>
        <div className="relative max-w-lg">
          <Bars />
          <h2 className="mt-8 text-3xl font-semibold leading-tight tracking-tight text-balance">
            從 YouTube 收集台語語音，
            <br />
            建立語者多元的研究語料。
          </h2>
          <p className="mt-4 text-[15px] leading-7 text-white/75">
            原生字幕篩選、音質分析、語者分段與分群、重複片頭偵測、人工審查，到可追溯的資料集匯出——全部在同一個工作台完成。
          </p>
        </div>
        <p className="relative text-xs text-white/55">僅供研究用途 · 請遵守各影片的授權條款</p>
      </aside>

      <main className="flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-[380px]">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid size-10 place-items-center rounded-xl bg-primary text-primary-fg">
              <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" aria-hidden>
                <rect x="3" y="9.5" width="2.6" height="5" rx="1.3" />
                <rect x="7.6" y="6.5" width="2.6" height="11" rx="1.3" />
                <rect x="12.2" y="3.5" width="2.6" height="17" rx="1.3" />
                <rect x="16.8" y="8" width="2.6" height="8" rx="1.3" />
              </svg>
            </div>
            <div>
              <div className="text-lg font-semibold leading-6">VOTI</div>
              <div className="text-xs text-fg-3">台語語料工作台</div>
            </div>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">登入</h1>
          <p className="mt-1.5 text-sm text-fg-3">使用管理員建立的帳號登入。沒有帳號請聯絡管理員。</p>

          <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
            {error && (
              <div role="alert" className="flex items-start gap-2 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2.5 text-[13px] text-danger-fg">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                {error}
              </div>
            )}
            <Field label="帳號" htmlFor="username">
              <Input id="username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} className="h-11 sm:h-10" autoFocus />
            </Field>
            <Field label="密碼" htmlFor="password">
              <div className="relative">
                <Input
                  id="password"
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-11 pr-11 sm:h-10"
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  className="absolute right-1 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-md text-fg-3 hover:bg-surface-2 hover:text-fg"
                  aria-label={show ? '隱藏密碼' : '顯示密碼'}
                >
                  {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </Field>
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>
              登入
            </Button>
          </form>
          {import.meta.env.VITE_MOCK === '1' && (
            <p className="mt-6 rounded-lg bg-surface-2 px-3 py-2 text-xs leading-5 text-fg-3">
              展示模式：帳號 <code className="font-mono text-fg-2">brian</code>（管理員）或 <code className="font-mono text-fg-2">yijun</code>（標註員），任意密碼。
            </p>
          )}
        </div>
      </main>
    </div>
  )
}
